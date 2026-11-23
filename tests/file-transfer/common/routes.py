import os
import re
from dataclasses import dataclass

from common.aws import aws_cli

DISPATCH_SECRET_BASE = "integration-hub-file-transfer/file-dispatch/"
WRITER_BASE = "integration-hub-file-transfer-"
PICKUP_ID = "[0-9a-f]{64}"

PUSH_TO_S3 = "push-to-s3"
PUSH_TO_S3_WITH_HOSTED_PICKUP = "push-to-s3-with-hosted-pickup"
PULL_FROM_PRESIGNED_URL = "pull-from-presigned-url"
ROUTE_NAMES = (
    PUSH_TO_S3,
    PUSH_TO_S3_WITH_HOSTED_PICKUP,
    PULL_FROM_PRESIGNED_URL,
)

# Defaults match MP's test-harness dispatch configuration; local runs may override them.
HOSTED_BUCKET_VARIABLE = "FILE_TRANSFER_HOSTED_PICKUP_BUCKET"
HOSTED_INCOMING_PREFIX_VARIABLE = "FILE_TRANSFER_HOSTED_PICKUP_INCOMING_PREFIX"
HOSTED_DESTINATION_PREFIX_VARIABLE = "FILE_TRANSFER_HOSTED_PICKUP_DESTINATION_PREFIX"
HOSTED_RETENTION_DAYS_VARIABLE = "FILE_TRANSFER_HOSTED_PICKUP_RETENTION_DAYS"
PRESIGNED_BUCKET_VARIABLE = "FILE_TRANSFER_PRESIGNED_PICKUP_BUCKET"
PRESIGNED_RECIPIENT_VARIABLE = "FILE_TRANSFER_PRESIGNED_RECIPIENT_ID"
PRESIGNED_INCOMING_PREFIX_VARIABLE = "FILE_TRANSFER_PRESIGNED_INCOMING_PREFIX"


@dataclass(frozen=True)
class Route:
    """Everything the tests need to know about one clean-file delivery pattern.

    Deliveries are always observed in S3.
    A route with a recipient_id is a hosted pickup bucket: the service chooses a
    per-execution directory, so the destination key cannot be known in advance.
    """

    name: str
    label: str
    incoming_prefix: str
    destination_bucket: str
    destination_prefix: str
    writer_function_name: str = ""
    writer_queue_name: str = ""
    verifies_object_metadata: bool = False
    recipient_id: str = ""
    retention_days: int = 0

    @property
    def dispatch_secret_name(self):
        return DISPATCH_SECRET_BASE + self.incoming_prefix

    @property
    def has_writer(self):
        return bool(self.writer_function_name)

    def listing_prefix(self, relative_key):
        """Return the S3 prefix to list when looking for the delivery of relative_key."""
        if self.recipient_id:
            return self.destination_prefix
        return self.destination_prefix + relative_key

    def run_prefix(self, run_token):
        """Return the prefix holding everything one test run delivered, if it has one."""
        if self.recipient_id:
            raise ValueError("Pickup routes choose their own directories; list the recipient prefix.")
        return f"{self.destination_prefix}{run_token}/"

    def find_delivery(self, keys, relative_key, delivered_name=None):
        """Return the delivered key for relative_key among keys, or None if absent.

        Direct routes match the exact key. Pickup routes match recipient/<id>/<name>.
        More than one match is a duplicate delivery and always an error.
        """
        if not self.recipient_id:
            expected = self.destination_prefix + relative_key
            return expected if expected in keys else None
        name = delivered_name or relative_key.rsplit("/", 1)[-1]
        pattern = re.compile(
            f"{re.escape(self.destination_prefix)}{PICKUP_ID}/{re.escape(name)}"
        )
        matches = sorted(key for key in keys if pattern.fullmatch(key))
        if len(matches) > 1:
            raise RuntimeError(f"{name} was delivered to {len(matches)} pickup directories.")
        return matches[0] if matches else None


def _required(environ, variable, purpose, default=""):
    value = environ.get(variable, default).strip()
    if not value:
        raise ValueError(f"Set {variable} to {purpose}.")
    return value


def _prefix(environ, variable, default):
    prefix = environ.get(variable, default).strip()
    if not prefix.endswith("/") or prefix.startswith("/"):
        raise ValueError(f"{variable} must end with '/' and must not start with '/'.")
    return prefix


def s3_route(environ=None):
    return Route(
        name=PUSH_TO_S3,
        label="direct upload to S3",
        incoming_prefix="test-harness/push-to-s3/",
        destination_bucket="integration-hub-file-transfer-test-test-harness",
        destination_prefix="push-to-s3/",
        writer_function_name=WRITER_BASE + PUSH_TO_S3,
        writer_queue_name=WRITER_BASE + PUSH_TO_S3,
        verifies_object_metadata=True,
    )


def hosted_pickup_route(environ=None):
    environ = os.environ if environ is None else environ
    retention = environ.get(HOSTED_RETENTION_DAYS_VARIABLE, "1").strip()
    if retention and (not retention.isdigit() or int(retention) < 1):
        raise ValueError(f"{HOSTED_RETENTION_DAYS_VARIABLE} must be a positive whole number.")
    return Route(
        name=PUSH_TO_S3_WITH_HOSTED_PICKUP,
        label="hosted pickup bucket",
        incoming_prefix=_prefix(
            environ, HOSTED_INCOMING_PREFIX_VARIABLE, "test-harness/hosted-pickup/"
        ),
        destination_bucket=_required(
            environ, HOSTED_BUCKET_VARIABLE, "the hosted pickup bucket for the test entry",
            default="integration-hub-test-test-harness-pickup",
        ),
        destination_prefix=_prefix(environ, HOSTED_DESTINATION_PREFIX_VARIABLE, "pickup/"),
        writer_function_name=WRITER_BASE + PUSH_TO_S3_WITH_HOSTED_PICKUP,
        writer_queue_name=WRITER_BASE + PUSH_TO_S3_WITH_HOSTED_PICKUP,
        verifies_object_metadata=True,
        retention_days=int(retention) if retention else 0,
    )


def presigned_route(environ=None):
    environ = os.environ if environ is None else environ
    recipient = _required(
        environ, PRESIGNED_RECIPIENT_VARIABLE, "the pickup recipient ID",
        default="test-harness-presigned",
    )
    if "/" in recipient:
        raise ValueError(f"{PRESIGNED_RECIPIENT_VARIABLE} must be a single path segment.")
    incoming_prefix = _prefix(
        environ, PRESIGNED_INCOMING_PREFIX_VARIABLE, "test-harness/pull-from-presigned-url/"
    )
    if PRESIGNED_BUCKET_VARIABLE in environ:
        bucket = _required(
            environ, PRESIGNED_BUCKET_VARIABLE, "the pickup bucket used by the notifier"
        )
    else:
        account = aws_cli("sts", "get-caller-identity").get("Account")
        if not isinstance(account, str) or not re.fullmatch(r"[0-9]{12}", account):
            raise RuntimeError("Could not determine a valid AWS account ID for the pickup bucket.")
        bucket = f"integration-hub-file-transfer-pickup-test-{account}"
    return Route(
        name=PULL_FROM_PRESIGNED_URL,
        label="pull from presigned URL",
        incoming_prefix=incoming_prefix,
        destination_bucket=bucket,
        destination_prefix=f"{recipient}/",
        recipient_id=recipient,
    )


ROUTE_FACTORIES = {
    PUSH_TO_S3: s3_route,
    PUSH_TO_S3_WITH_HOSTED_PICKUP: hosted_pickup_route,
    PULL_FROM_PRESIGNED_URL: presigned_route,
}


def select_route(route_name, environ=None):
    """Return a test-account route, resolving the presigned bucket's account ID if needed."""
    try:
        factory = ROUTE_FACTORIES[route_name]
    except KeyError:
        raise ValueError(f"Unknown route: {route_name}.") from None
    return factory(environ)
