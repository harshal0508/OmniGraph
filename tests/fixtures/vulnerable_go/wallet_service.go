// wallet_service.go
// ---------------------------------------------------------------------------
// FIXTURE: Go service demonstrating distributed race conditions.
//
// SCENARIO:
//   A wallet microservice built in Go using gorm for ORM and go-redis for
//   caching. Two goroutines (or two replicas) simultaneously call Withdraw().
//   Both read balance, both check sufficiency, both deduct, both save.
//   One deduction is lost.
//
// EXPECTED DETECTION:
//   - READS_FROM edge (gorm.First)
//   - WRITES_TO edge (gorm.Save)
//   - Redis: READS_FROM (Redis.get) + WRITES_TO (Redis.set) -> TOCTOU on Redis
//   - Severity: CRITICAL (replicas=5 from K8s fixture)
// ---------------------------------------------------------------------------

package wallet

import (
    "context"
    "fmt"

    "github.com/go-redis/redis/v9"
    "gorm.io/gorm"
)

type Wallet struct {
    gorm.Model
    UserID  uint
    Balance float64
}

type WalletService struct {
    db    *gorm.DB
    redis *redis.Client
}

// Withdraw deducts amount from user wallet.
//
// BUG: No row-level lock (db.Set("gorm:query_option", "FOR UPDATE").First(...))
// Two concurrent requests both read balance=100.00, both see sufficient funds,
// both deduct 80.00, both call Save() -> balance ends at 20.00 instead of -60.00
// (which would correctly fail the second withdrawal).
func (s *WalletService) Withdraw(ctx context.Context, userID uint, amount float64) error {
    var wallet Wallet

    // READS_FROM (gorm.First) — start of TOCTOU window
    if err := s.db.First(&wallet, "user_id = ?", userID).Error; err != nil {
        return fmt.Errorf("wallet not found: %w", err)
    }

    if wallet.Balance < amount {
        return fmt.Errorf("insufficient funds")
    }

    wallet.Balance -= amount   // CHECK + modify — race window

    // WRITES_TO (gorm.Save) — closes window unprotected
    if err := s.db.Save(&wallet).Error; err != nil {
        return fmt.Errorf("failed to save wallet: %w", err)
    }

    return nil
}

// UpdateCachedBalance reads and writes to Redis without WATCH.
//
// BUG: GET then SET pattern without WATCH/MULTI — Redis TOCTOU.
// Two replicas both GET "wallet:42" = "100.00", both subtract, both SET.
// One SET overwrites the other silently.
func (s *WalletService) UpdateCachedBalance(ctx context.Context, userID uint, delta float64) error {
    key := fmt.Sprintf("wallet:%d", userID)

    // READS_FROM Redis (Redis.get)
    val, err := s.redis.Get(ctx, key).Float64()
    if err != nil {
        return err
    }

    newBalance := val + delta

    // WRITES_TO Redis (Redis.set) — non-atomic r-m-w race
    return s.redis.Set(ctx, key, newBalance, 0).Err()
}

// CreateTransaction inserts a transaction record.
//
// BUG: Non-idempotent INSERT — retry hazard.
// If network timeout occurs after insert but before ACK, retry creates duplicate.
func (s *WalletService) CreateTransaction(ctx context.Context, userID uint, amount float64) error {
    tx := map[string]interface{}{
        "user_id": userID,
        "amount":  amount,
    }
    // WRITES_TO (gorm.Create — non-idempotent INSERT)
    return s.db.Create(&tx).Error
}
