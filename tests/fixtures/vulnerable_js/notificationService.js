// notificationService.js
// ─────────────────────────────────────────────────────────────────────────────
// FIXTURE: Second JavaScript service — TypeORM style
//
// SCENARIO: Notification service that ALSO writes to the users table
//           to mark notifications as sent. Running as 3 replicas.
//           No distributed lock — races with paymentController.js.
//
// EXPECTED DETECTION:
//   - Second WRITES_TO edge pointing to the same shared DB
//   - Cross-service collision with paymentController.js
//   - Shared target: users / transactions table
// ─────────────────────────────────────────────────────────────────────────────

const { getRepository } = require('typeorm');
const { User } = require('./entities/User');

/**
 * Mark notification as sent — writes to users table.
 *
 * BUG: Concurrent replicas can both read the user record,
 * both set notificationSent=true, and both call .save() —
 * triggering duplicate notifications.
 */
async function markNotificationSent(userId) {
  const userRepo = getRepository(User);
  const user = await userRepo.findOne({ where: { id: userId } });  // READS_FROM (TypeORM)

  user.notificationSent = true;
  user.lastNotifiedAt   = new Date();
  await userRepo.save(user);   // WRITES_TO (TypeORM) — RACES with paymentController
}

/**
 * Bulk update notification preferences.
 */
async function bulkUpdatePreferences(userIds, prefs) {
  const userRepo = getRepository(User);
  await userRepo.update(userIds, prefs);   // WRITES_TO (TypeORM)
}

module.exports = { markNotificationSent, bulkUpdatePreferences };
