import json
import math
import subprocess


INCOMING_BUCKET = "integration-hub-file-transfer-test-incoming"


CLEAN_BUCKET = "integration-hub-file-transfer-test-clean"


DESTINATION_BUCKET = "integration-hub-file-transfer-test-test-harness"


INCOMING_PREFIX = "test-harness/push-to-s3/"


DESTINATION_PREFIX = "push-to-s3/"


REGION = "eu-west-2"


def aws_cli(service, operation, *arguments, timeout=30):
    """Run an AWS CLI command with a time limit and return parsed JSON output.

    Use the AWS CLI's current credentials and avoid exposing command output in
    error messages.
    """
    try:
        result = subprocess.run(
            [
                "aws", service, operation, *arguments,
                "--region", REGION,
                "--output", "json",
                "--no-cli-pager",
                "--cli-connect-timeout", "10",
                "--cli-read-timeout", str(math.ceil(timeout)),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"AWS {operation} failed or timed out.") from error
    return json.loads(result.stdout)


def aws_s3(operation, *arguments, timeout=30):
    """Run an S3 command through the shared AWS command/error handling helper."""
    return aws_cli("s3api", operation, *arguments, timeout=timeout)
