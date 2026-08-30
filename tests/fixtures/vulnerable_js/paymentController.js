// paymentController.js
// ─────────────────────────────────────────────────────────────────────────────
// FIXTURE: Vulnerable JavaScript service — Sequelize + raw SQL
//
// SCENARIO: Payment microservice deployed as 5 Kubernetes replicas.
//           Two concurrent requests both call processRefund() which
//           reads wallet balance and calls wallet.save() with no lock.
//
// EXPECTED DETECTION:
//   - WRITES_TO edge from svc_vulnerable_js to shared PostgreSQL
//   - Cross-service collision with notificationService.js
//   - CRITICAL severity (replica_count=5 from k8s fixture)
// ─────────────────────────────────────────────────────────────────────────────

const { Wallet, Transaction } = require('./models');
const db = require('./db');

/**
 * Process a refund for a user.
 *
 * BUG: No transaction wrapper, no SELECT FOR UPDATE.
 * Two replicas can both read balance=100, both add refund=50,
 * and both call wallet.save() => wallet ends up with balance=150
 * instead of 200 (one update is lost).
 */
async function processRefund(userId, refundAmount) {
  const wallet = await Wallet.findOne({ where: { userId } });  // READS_FROM (Sequelize)

  if (wallet.balance >= 0) {
    wallet.balance += refundAmount;
    await wallet.save();           // WRITES_TO (Sequelize) — RACE CONDITION
    return { success: true, newBalance: wallet.balance };
  }

  return { success: false, error: 'Negative balance' };
}

/**
 * Raw SQL write — direct INSERT without lock.
 * OmniGraph should detect the INSERT keyword via SQL analysis.
 */
async function logTransaction(userId, amount, type) {
  await db.query(
    `INSERT INTO transactions (user_id, amount, type, created_at)
     VALUES ($1, $2, $3, NOW())`,
    [userId, amount, type]
  );
}

/**
 * Update user tier — another unprotected write.
 */
async function updateUserTier(userId, newTier) {
  await db.query(
    `UPDATE users SET tier = $1 WHERE id = $2`,
    [newTier, userId]
  );   // WRITES_TO via raw SQL — RACE CONDITION (no transaction)
}

module.exports = { processRefund, logTransaction, updateUserTier };
