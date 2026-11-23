import json
import logging
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orders")

sns = boto3.client("sns")
topic_arn = os.environ["TOPIC_ARN"]


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok"})
        elif self.path == "/":
            self.respond(200, {
                "application": "SIS3 Orders - Darkhan",
                "endpoint": "POST /orders",
            })
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/orders":
            self.respond(404, {"error": "Not found"})
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("Request body must be between 1 and 16384 bytes")

            order = json.loads(self.rfile.read(size))
            if not isinstance(order, dict):
                raise ValueError("Body must be a JSON object")

            order_id = order.get("orderId", str(uuid.uuid4()))
            product = order.get("product")
            quantity = order.get("quantity")

            if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 100:
                raise ValueError("Invalid orderId")
            if not isinstance(product, str) or not product.strip() or len(product) > 200:
                raise ValueError("Invalid product")
            if type(quantity) is not int or not 1 <= quantity <= 100:
                raise ValueError("Quantity must be an integer from 1 to 100")

        except (ValueError, UnicodeDecodeError):
            self.respond(400, {
                "error": "Provide valid JSON with product and integer quantity (1-100); orderId is optional",
            })
            return

        event = {
            "eventType": "OrderPlaced",
            "orderId": order_id,
            "product": product,
            "quantity": quantity,
        }

        try:
            sns.publish(
                TopicArn=topic_arn,
                Message=json.dumps(event),
            )
        except Exception:
            logger.exception("Failed to publish orderId=%s", order_id)
            self.respond(503, {
                "error": "Could not confirm publication; retry with the same orderId",
                "orderId": order_id,
            })
            return

        logger.info(json.dumps({
            "event": "order_published",
            "orderId": order_id,
        }))
        self.respond(202, {
            "orderId": order_id,
            "status": "ACCEPTED",
        })


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    logger.info("Orders application listening on port 8080")
    server.serve_forever()
