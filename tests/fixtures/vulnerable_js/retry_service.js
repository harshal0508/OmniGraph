// retry_service.js
// ---------------------------------------------------------------------------
// FIXTURE: JavaScript service demonstrating retry-amplification hazard.
//
// SCENARIO:
//   An event logging service that fires an analytics event on every user
//   action. It uses an INSERT (non-idempotent write). The service has a
//   retry wrapper (exponential backoff) for network reliability. Running
//   as 5 Kubernetes replicas.
//
// EXPECTED DETECTION:
//   - WRITES_TO edge from Knex INSERT (non-idempotent)
//   - replica_count=5 from K8s fixture
//   - Retry-Amplification Hazard WARNING
//   - Remediation: add idempotency key + ON CONFLICT DO NOTHING
// ---------------------------------------------------------------------------

const knex  = require("knex");
const db    = knex({ client: "pg" });

/**
 * Log an analytics event.
 *
 * BUG: INSERT without idempotency key.
 * If this times out after the server inserts but before the client gets ACK,
 * the retry will create a duplicate event row.
 * With 5 replicas each potentially retrying 3x = up to 15 inserts for 1 event.
 */
async function logEvent(userId, eventType, metadata) {
  await db("analytics_events").insert({   // WRITES_TO (Knex.insert - non-idempotent)
    user_id:    userId,
    event_type: eventType,
    metadata:   JSON.stringify(metadata),
    created_at: new Date(),
  });
}

/**
 * Charge a user — another non-idempotent write.
 * A timeout + retry here means double-charge.
 */
async function recordCharge(userId, amount, currency) {
  await db("charges").insert({            // WRITES_TO (Knex.insert - non-idempotent)
    user_id:   userId,
    amount:    amount,
    currency:  currency,
    status:    "completed",
    charged_at: new Date(),
  });
}

/**
 * Safe pattern — upsert with a unique request_id.
 * This IS idempotent and would NOT be flagged.
 */
async function safeRecordCharge(requestId, userId, amount) {
  await db("charges")
    .insert({ request_id: requestId, user_id: userId, amount })
    .onConflict("request_id")
    .ignore();     // INSERT ... ON CONFLICT DO NOTHING — safe to retry
}

module.exports = { logEvent, recordCharge, safeRecordCharge };
