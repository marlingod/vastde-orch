"""
VAST DataEngine Fraud Scorer Function
======================================

Consumes transactions from the fraud.transactions.raw Kafka topic via
S3/element trigger (Blob Expansion writes to a table, which triggers this
function). Scores each transaction for fraud and publishes results to:
  - fraud.transactions.scored  (all transactions + risk score)
  - fraud.alerts               (high-risk transactions only)

Deployed as a VAST DataEngine pipeline function.
"""

import json
import math
import os
import time

# ---------------------------------------------------------------------------
# Fraud detection rules (same logic as generator/fraud_patterns.py)
# ---------------------------------------------------------------------------
ALERT_THRESHOLD = 0.8

RULE_WEIGHTS = {
    "velocity": 0.25,
    "geographic": 0.30,
    "amount": 0.20,
    "card_testing": 0.15,
    "fraud_ring": 0.10,
}

FRAUD_RING_MERCHANTS = {
    "MER-FR-SHELL-001", "MER-FR-QUICKMART-001", "MER-FR-LUXGOODS-001",
    "MER-FR-CRYPTOATM-001", "MER-FR-GIFTCARD-001",
}


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_transaction(txn, recent_txns=None, customer_avg_spend=None):
    """Score a single transaction. Returns (risk_score, triggered_rules)."""
    scores = {}
    triggered = []

    # 1. Velocity check — count recent transactions for same card
    if recent_txns:
        same_card = [t for t in recent_txns if t.get("card_id") == txn.get("card_id")]
        velocity = min(len(same_card) / 10.0, 1.0)
        scores["velocity"] = velocity
        if velocity > 0.5:
            triggered.append("velocity_attack")
    else:
        scores["velocity"] = 0.0

    # 2. Geographic impossibility — distant cities in short time
    if recent_txns:
        same_card = [t for t in recent_txns if t.get("card_id") == txn.get("card_id")]
        geo_score = 0.0
        for prev in same_card[-3:]:
            dist = _haversine_km(
                txn.get("location_lat", 0), txn.get("location_lon", 0),
                prev.get("location_lat", 0), prev.get("location_lon", 0),
            )
            if dist > 500:
                geo_score = 1.0
                triggered.append("geographic_impossibility")
                break
        scores["geographic"] = geo_score
    else:
        scores["geographic"] = 0.0

    # 3. Amount anomaly — compared to customer average
    amount = txn.get("amount", 0)
    if customer_avg_spend and customer_avg_spend > 0:
        ratio = amount / customer_avg_spend
        scores["amount"] = min(ratio / 10.0, 1.0)
        if ratio > 10:
            triggered.append("amount_anomaly")
    else:
        scores["amount"] = 0.0

    # 4. Card testing — small amounts ($1-$3)
    if 0.50 <= amount <= 3.00:
        scores["card_testing"] = 0.8
        triggered.append("card_testing")
    else:
        scores["card_testing"] = 0.0

    # 5. Fraud ring merchant
    merchant = txn.get("merchant_id", "")
    if merchant in FRAUD_RING_MERCHANTS:
        scores["fraud_ring"] = 1.0
        triggered.append("fraud_ring")
    else:
        scores["fraud_ring"] = 0.0

    # Weighted composite
    risk_score = sum(scores[k] * RULE_WEIGHTS[k] for k in RULE_WEIGHTS)

    # Boost: if any single rule is very high confidence, use max of weighted and single rule
    max_single = max(scores.values()) if scores else 0
    if max_single >= 0.8:
        risk_score = max(risk_score, max_single)

    return round(min(risk_score, 1.0), 4), triggered


# ---------------------------------------------------------------------------
# DataEngine function interface
# ---------------------------------------------------------------------------
# In-memory sliding window of recent transactions for scoring context
_recent_txns = []
_MAX_RECENT = 1000
_kafka_producer = None


def init(ctx):
    """One-time initialization when the function container starts."""
    global _kafka_producer

    ctx.logger.info("Fraud Scorer initializing...")

    kafka_bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "<VAST_EVENT_BROKER_VIP>:9092")
    ctx.logger.info(f"Kafka bootstrap: {kafka_bootstrap}")

    # Initialize Kafka producer for publishing scored transactions and alerts
    try:
        from confluent_kafka import Producer
        _kafka_producer = Producer({
            "bootstrap.servers": kafka_bootstrap,
            "message.timeout.ms": 10000,
        })
        ctx.logger.info("Kafka producer initialized")
    except Exception as e:
        ctx.logger.error(f"Failed to initialize Kafka producer: {e}")
        _kafka_producer = None

    ctx.logger.info("Fraud Scorer ready")


def handler(ctx, event):
    """Process each incoming event (triggered by S3 element create or cron)."""
    global _recent_txns

    start_time = time.perf_counter()
    ctx.logger.info(f"Event received: {type(event)}")

    # Parse the event data
    try:
        if hasattr(event, "data"):
            event_data = event.data
        else:
            event_data = event

        # The event could be an S3 element trigger (CloudEvent with Records[])
        # or a direct transaction dict. Handle both.
        if isinstance(event_data, dict) and "Records" in event_data:
            # S3 element trigger — transaction data may be in top-level fields
            # (sent via kafka-hacks send-test-event --data) or embedded in Records
            ctx.logger.info("S3 element event received")

            # Check for top-level transaction fields (from --data flag)
            if "transaction_id" in event_data:
                txn = event_data
                ctx.logger.info(f"Transaction found in top-level fields: {txn.get('transaction_id')}")
            else:
                # No transaction data in this event — just an S3 notification
                ctx.logger.info("No transaction data in event — acknowledging")
                return {"status": "s3_event_acknowledged"}
        elif isinstance(event_data, dict) and "transaction_id" in event_data:
            # Direct transaction (top-level fields without Records envelope)
            txn = event_data
        else:
            # Try to parse as JSON string
            txn = event_data if isinstance(event_data, dict) else json.loads(event_data)

    except Exception as e:
        ctx.logger.error(f"Failed to parse event: {e}")
        return {"error": str(e)}

    # Skip non-transaction events (metrics, test messages)
    if "transaction_id" not in txn:
        return {"status": "skipped", "reason": "not a transaction"}

    # Score the transaction
    risk_score, triggered_rules = score_transaction(
        txn,
        recent_txns=_recent_txns,
        customer_avg_spend=txn.get("amount", 100),  # Simplified — production would query customer table
    )

    # Build scored transaction
    scored_txn = {
        **txn,
        "risk_score": risk_score,
        "triggered_rules": triggered_rules,
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "scoring_latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
    }

    # Publish to fraud.transactions.scored
    if _kafka_producer:
        try:
            _kafka_producer.produce(
                "fraud.transactions.scored",
                key=txn.get("card_id", "").encode("utf-8"),
                value=json.dumps(scored_txn).encode("utf-8"),
            )

            # Publish alerts for high-risk transactions
            if risk_score >= ALERT_THRESHOLD:
                alert = {
                    "transaction_id": txn["transaction_id"],
                    "card_id": txn.get("card_id"),
                    "amount": txn.get("amount"),
                    "risk_score": risk_score,
                    "triggered_rules": triggered_rules,
                    "fraud_type": triggered_rules[0] if triggered_rules else "unknown",
                    "merchant_id": txn.get("merchant_id"),
                    "location_city": txn.get("location_city"),
                    "timestamp": txn.get("timestamp"),
                    "alerted_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                }
                _kafka_producer.produce(
                    "fraud.alerts",
                    key=txn.get("card_id", "").encode("utf-8"),
                    value=json.dumps(alert).encode("utf-8"),
                )
                ctx.logger.info(
                    f"ALERT: {txn['transaction_id']} score={risk_score:.2f} "
                    f"rules={triggered_rules}"
                )

            _kafka_producer.poll(0)
        except Exception as e:
            ctx.logger.error(f"Failed to publish: {e}")

    # Update sliding window
    _recent_txns.append(txn)
    if len(_recent_txns) > _MAX_RECENT:
        _recent_txns = _recent_txns[-_MAX_RECENT:]

    latency = round((time.perf_counter() - start_time) * 1000, 2)
    ctx.logger.info(
        f"Scored: {txn['transaction_id']} risk={risk_score:.2f} "
        f"rules={triggered_rules} latency={latency}ms"
    )

    return {
        "transaction_id": txn["transaction_id"],
        "risk_score": risk_score,
        "triggered_rules": triggered_rules,
        "latency_ms": latency,
    }
