import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3
from aws_xray_sdk.core import patch_all
from botocore.exceptions import ClientError

# Instrument boto3 so every DynamoDB call becomes a subsegment in the trace.
patch_all()

logger = logging.getLogger()
logger.setLevel(logging.INFO)

STAGE = os.environ.get("STAGE", "dev")
NAMESPACE = "CourseProject/Orders"

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def log(level, event, **fields):
    # One JSON object per line: Logs Insights discovers the fields automatically.
    print(json.dumps({"level": level, "event": event, **fields}))


def emit_metrics(processed, duplicates, failed):
    # Embedded Metric Format: CloudWatch turns this log line into custom metrics.
    # IDs stay in the logs; the only dimension is Stage.
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Stage"]],
                "Metrics": [
                    {"Name": "OrdersProcessed", "Unit": "Count"},
                    {"Name": "OrdersDuplicate", "Unit": "Count"},
                    {"Name": "OrdersFailed", "Unit": "Count"},
                ],
            }],
        },
        "Stage": STAGE,
        "OrdersProcessed": processed,
        "OrdersDuplicate": duplicates,
        "OrdersFailed": failed,
    }))


def process_order(body):
    order = json.loads(body)

    if not isinstance(order, dict):
        raise ValueError("Order must be a JSON object")

    if order.get("eventType") != "OrderPlaced":
        raise ValueError("Expected OrderPlaced event")

    order_id = order.get("orderId")
    product = order.get("product")
    quantity = order.get("quantity")

    if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 100:
        raise ValueError("Invalid orderId")

    if not isinstance(product, str) or not product.strip() or len(product) > 200:
        raise ValueError("Invalid product")

    if type(quantity) is not int or not 1 <= quantity <= 100:
        raise ValueError("Quantity must be an integer from 1 to 100")

    try:
        table.put_item(
            Item={
                "orderId": order_id,
                "product": product,
                "quantity": quantity,
                "status": "PROCESSED",
                "processedAt": datetime.now(timezone.utc).isoformat(),
            },
            ConditionExpression="attribute_not_exists(orderId)",
        )
        log("INFO", "order_processed", orderId=order_id)
        return "processed"
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        log("INFO", "duplicate_skipped", orderId=order_id)
        return "duplicate"


def handler(event, context):
    failures = []
    processed = duplicates = 0

    for record in event["Records"]:
        try:
            if process_order(record["body"]) == "duplicate":
                duplicates += 1
            else:
                processed += 1
        except Exception as error:
            # Handled failure: the Lambda Errors metric stays at 0, so we
            # publish our own OrdersFailed metric and log the details.
            log(
                "ERROR",
                "order_failed",
                messageId=record["messageId"],
                errorType=type(error).__name__,
                error=str(error)[:200],
            )
            failures.append({"itemIdentifier": record["messageId"]})

    emit_metrics(processed, duplicates, len(failures))
    return {"batchItemFailures": failures}
