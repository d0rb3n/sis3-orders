import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


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
        logger.info(json.dumps({
            "event": "order_processed",
            "orderId": order_id,
        }))
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        logger.info(json.dumps({
            "event": "duplicate_skipped",
            "orderId": order_id,
        }))


def handler(event, context):
    failures = []

    for record in event["Records"]:
        try:
            process_order(record["body"])
        except Exception:
            logger.exception(
                "Order processing failed: messageId=%s",
                record["messageId"],
            )
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
