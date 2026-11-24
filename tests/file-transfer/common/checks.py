from common.aws import aws_s3


def lifecycle_expiry_days(rules, key_prefix):
    """Return the expiry days of enabled rules that cover key_prefix.

    Prefix-only rules must cover the whole key_prefix. Overlapping tag/size
    filters and covering date-based expiration fail closed because they cannot
    guarantee a fixed retention day count.
    """
    days = []
    for rule in rules:
        if rule.get("Status") != "Enabled":
            continue
        expiration = rule.get("Expiration", {})
        if expiration.get("Days") is None and expiration.get("Date") is None:
            continue
        rule_filter = rule.get("Filter", {})
        rule_prefix = rule_filter.get(
            "Prefix", rule_filter.get("And", {}).get("Prefix", rule.get("Prefix", ""))
        )
        if set(rule_filter) - {"Prefix"}:
            if key_prefix.startswith(rule_prefix) or rule_prefix.startswith(key_prefix):
                raise RuntimeError(
                    f"Lifecycle rule for {rule_prefix or 'the bucket'} uses tag or size filters, "
                    "so effective retention cannot be verified for the pickup prefix."
                )
            continue
        if not key_prefix.startswith(rule_prefix):
            continue
        if expiration.get("Date") is not None:
            raise RuntimeError(
                f"Lifecycle rule for {rule_prefix or 'the bucket'} uses date-based "
                "expiration, which cannot be verified against an expected day count."
            )
        if expiration.get("Days") is not None:
            days.append(expiration["Days"])
    return days


def verify_retention_lifecycle(route):
    """Check the hosted bucket expires deliveries after the configured retention days.

    Skipped, and said to be skipped, when the expected retention is not supplied.
    """
    if not route.retention_days:
        print("SKIPPED: retention lifecycle check (no expected retention days supplied).", flush=True)
        return
    configuration = aws_s3("get-bucket-lifecycle-configuration", "--bucket", route.destination_bucket)
    days = lifecycle_expiry_days(configuration.get("Rules", []), route.destination_prefix)
    if not days:
        raise RuntimeError(
            f"No enabled lifecycle rule expires {route.destination_prefix} objects "
            f"after {route.retention_days} days."
        )
    effective_days = min(days)
    if effective_days != route.retention_days:
        raise RuntimeError(
            f"Lifecycle expires {route.destination_prefix} objects after {effective_days} days, "
            f"expected {route.retention_days} days."
        )
    print(f"PASS: lifecycle expires hosted files after {route.retention_days} days.", flush=True)
