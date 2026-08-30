"""
core/ingestion/orm_semantic_map.py
──────────────────────────────────
ORM Semantic Map — maps ORM method calls to graph edge types.

Because AST parsers see `user.save()` not `INSERT INTO users`,
we need a lookup table that resolves framework method names to
their underlying write/read semantics.

Covers:
  • Python: Django ORM, SQLAlchemy, Tortoise ORM
  • JavaScript/TypeScript: Sequelize, TypeORM, Prisma, Mongoose, Knex
"""

from __future__ import annotations
from core.schema import EdgeType


# ─── Python ORM Map ───────────────────────────────────────────────────────────

PYTHON_ORM_WRITES: dict[str, str] = {
    # Django ORM
    "save":          "Django.save()",
    "create":        "Django.create()",
    "update":        "Django.update()",
    "bulk_create":   "Django.bulk_create()",
    "bulk_update":   "Django.bulk_update()",
    "get_or_create": "Django.get_or_create()",
    "update_or_create": "Django.update_or_create()",
    "delete":        "Django.delete()",
    # SQLAlchemy
    "add":           "SQLAlchemy.session.add()",
    "merge":         "SQLAlchemy.session.merge()",
    "flush":         "SQLAlchemy.session.flush()",
    "commit":        "SQLAlchemy.session.commit()",
    "execute":       "SQLAlchemy.execute()" ,
    # Tortoise ORM
    "create":        "Tortoise.create()",
    "save":          "Tortoise.save()",
}

PYTHON_ORM_READS: dict[str, str] = {
    # Django ORM
    "filter":     "Django.filter()",
    "get":        "Django.get()",
    "get_object": "Django.get_object()",
    "get_object_or_404": "Django.get_object_or_404()",
    "all":        "Django.all()",
    "first":      "Django.first()",
    "last":       "Django.last()",
    "exists":     "Django.exists()",
    "values":     "Django.values()",
    "aggregate":  "Django.aggregate()",
    # SQLAlchemy
    "query":      "SQLAlchemy.session.query()",
    "select":     "SQLAlchemy.select()",
    "scalars":    "SQLAlchemy.scalars()",
    "scalar":     "SQLAlchemy.scalar()",
}

PYTHON_LOCK_PATTERNS: dict[str, str] = {
    "select_for_update": "Django.select_for_update()",
    "with_for_update":   "SQLAlchemy.with_for_update()",
    "acquire":           "threading.Lock.acquire()",
    "advisory_lock":     "pg_advisory_lock()",
}

PYTHON_TRANSACTION_PATTERNS: dict[str, str] = {
    "atomic":       "Django.transaction.atomic()",
    "begin":        "SQLAlchemy.session.begin()",
    "savepoint":    "SQLAlchemy.session.begin_nested()",
    "transaction":  "Sequelize.transaction()",
}

PYTHON_SQL_WRITE_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "UPSERT", "MERGE", "TRUNCATE",
)

PYTHON_SQL_READ_KEYWORDS = ("SELECT",)


# ─── JavaScript / TypeScript ORM Map ─────────────────────────────────────────

JS_ORM_WRITES: dict[str, str] = {
    # Sequelize
    "create":           "Sequelize.create()",
    "bulkCreate":       "Sequelize.bulkCreate()",
    "update":           "Sequelize.update()",
    "upsert":           "Sequelize.upsert()",
    "destroy":          "Sequelize.destroy()",
    "save":             "Sequelize.save()",
    # TypeORM
    "save":             "TypeORM.save()",
    "insert":           "TypeORM.insert()",
    "update":           "TypeORM.update()",
    "delete":           "TypeORM.delete()",
    "remove":           "TypeORM.remove()",
    # Prisma
    "create":           "Prisma.create()",
    "createMany":       "Prisma.createMany()",
    "update":           "Prisma.update()",
    "updateMany":       "Prisma.updateMany()",
    "upsert":           "Prisma.upsert()",
    "delete":           "Prisma.delete()",
    "deleteMany":       "Prisma.deleteMany()",
    # Mongoose
    "save":             "Mongoose.save()",
    "findByIdAndUpdate": "Mongoose.findByIdAndUpdate()",
    "findOneAndUpdate":  "Mongoose.findOneAndUpdate()",
    "findOneAndDelete":  "Mongoose.findOneAndDelete()",
    "insertMany":        "Mongoose.insertMany()",
    # Knex
    "insert":           "Knex.insert()",
    "update":           "Knex.update()",
    "del":              "Knex.del()",
}

JS_ORM_READS: dict[str, str] = {
    # Sequelize
    "findOne":    "Sequelize.findOne()",
    "findAll":    "Sequelize.findAll()",
    "findByPk":   "Sequelize.findByPk()",
    "count":      "Sequelize.count()",
    # TypeORM
    "find":       "TypeORM.find()",
    "findOne":    "TypeORM.findOne()",
    "findBy":     "TypeORM.findBy()",
    "count":      "TypeORM.count()",
    # Prisma
    "findUnique": "Prisma.findUnique()",
    "findMany":   "Prisma.findMany()",
    "findFirst":  "Prisma.findFirst()",
    # Mongoose
    "find":       "Mongoose.find()",
    "findOne":    "Mongoose.findOne()",
    "findById":   "Mongoose.findById()",
    # Knex
    "select":     "Knex.select()",
    "where":      "Knex.where()",
}

JS_LOCK_PATTERNS: dict[str, str] = {
    "lock":        "RedisLock.lock()",
    "acquire":     "Mutex.acquire()",
    "withLock":    "p-mutex.withLock()",
    "transaction": "Sequelize.transaction()",
}

JS_TRANSACTION_PATTERNS: dict[str, str] = {
    "transaction":  "Sequelize/TypeORM.transaction()",
    "beginTransaction": "raw.beginTransaction()",
    "startTransaction": "TypeORM.startTransaction()",
}

JS_SQL_WRITE_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "UPSERT", "MERGE", "TRUNCATE",
)

JS_SQL_READ_KEYWORDS = ("SELECT",)


# ─── Unified Resolver ─────────────────────────────────────────────────────────

def resolve_method_to_edge(
    method_name: str,
    language: str,
) -> tuple[EdgeType | None, str | None]:
    """
    Given a method name and source language, returns:
      (EdgeType, human_readable_pattern_name) or (None, None) if not recognized.

    Used by the AST parser to convert ORM method call nodes
    into graph edges without needing to know the ORM framework.
    """
    lang = language.lower()

    if lang in ("python",):
        writes = PYTHON_ORM_WRITES
        reads  = PYTHON_ORM_READS
        locks  = PYTHON_LOCK_PATTERNS
        txns   = PYTHON_TRANSACTION_PATTERNS
    elif lang in ("javascript", "typescript"):
        writes = JS_ORM_WRITES
        reads  = JS_ORM_READS
        locks  = JS_LOCK_PATTERNS
        txns   = JS_TRANSACTION_PATTERNS
    else:
        return None, None

    if method_name in writes:
        return EdgeType.WRITES_TO, writes[method_name]
    if method_name in reads:
        return EdgeType.READS_FROM, reads[method_name]
    if method_name in locks:
        return EdgeType.USES_LOCK, locks[method_name]
    if method_name in txns:
        return EdgeType.USES_TRANSACTION, txns[method_name]

    return None, None


def is_sql_write(sql_string: str) -> bool:
    """Returns True if a raw SQL string is a write operation."""
    upper = sql_string.strip().upper()
    return any(upper.startswith(kw) for kw in PYTHON_SQL_WRITE_KEYWORDS + JS_SQL_WRITE_KEYWORDS)


def is_sql_read(sql_string: str) -> bool:
    """Returns True if a raw SQL string is a read operation."""
    upper = sql_string.strip().upper()
    return upper.startswith("SELECT")


# ─── Go ORM Map ───────────────────────────────────────────────────────────────
# Covers: database/sql, gorm, sqlx

GO_ORM_WRITES: dict[str, str] = {
    # database/sql
    "exec":          "sql.Exec()",
    # gorm
    "create":        "gorm.Create()",
    "save":          "gorm.Save()",
    "update":        "gorm.Update()",
    "updates":       "gorm.Updates()",
    "delete":        "gorm.Delete()",
    "createinbatches": "gorm.CreateInBatches()",
    # sqlx
    "exec":          "sqlx.Exec()",
    "namedexec":     "sqlx.NamedExec()",
    "mustexec":      "sqlx.MustExec()",
}

GO_ORM_READS: dict[str, str] = {
    # database/sql
    "query":         "sql.Query()",
    "queryrow":      "sql.QueryRow()",
    "queryrowcontext": "sql.QueryRowContext()",
    "querycontext":  "sql.QueryContext()",
    # gorm
    "find":          "gorm.Find()",
    "first":         "gorm.First()",
    "last":          "gorm.Last()",
    "take":          "gorm.Take()",
    "findinfirstbatch": "gorm.FindInBatches()",
    # sqlx
    "get":           "sqlx.Get()",
    "select":        "sqlx.Select()",
    "named":         "sqlx.Named()",
}

GO_LOCK_PATTERNS: dict[str, str] = {
    "lock":          "sync.Mutex.Lock()",
    "rlock":         "sync.RWMutex.RLock()",
    "trylock":       "sync.Mutex.TryLock()",
    "acquire":       "redislock.Acquire()",
    "obtain":        "redsync.Obtain()",    # go-redis/redsync
}

GO_TRANSACTION_PATTERNS: dict[str, str] = {
    "begin":         "sql.Begin()",
    "begintx":       "sql.BeginTx()",
    "transaction":   "gorm.Transaction()",
}


# ─── Redis Command Map ────────────────────────────────────────────────────────
# Covers redis-py (Python), ioredis / node-redis (JS), go-redis (Go)

REDIS_WRITES: dict[str, str] = {
    # String writes (potentially non-atomic r-m-w)
    "set":           "Redis.set()",
    "mset":          "Redis.mset()",
    "setex":         "Redis.setex()",
    "psetex":        "Redis.psetex()",
    "getset":        "Redis.getset()",
    # Hash writes
    "hset":          "Redis.hset()",
    "hmset":         "Redis.hmset()",
    "hdel":          "Redis.hdel()",
    # List writes
    "lpush":         "Redis.lpush()",
    "rpush":         "Redis.rpush()",
    "lset":          "Redis.lset()",
    "lrem":          "Redis.lrem()",
    # Set writes
    "sadd":          "Redis.sadd()",
    "srem":          "Redis.srem()",
    # Sorted set writes
    "zadd":          "Redis.zadd()",
    "zrem":          "Redis.zrem()",
    # Key mgmt
    "del":           "Redis.del()",
    "delete":        "Redis.delete()",
    "expire":        "Redis.expire()",
}

REDIS_READS: dict[str, str] = {
    "get":           "Redis.get()",
    "mget":          "Redis.mget()",
    "hget":          "Redis.hget()",
    "hmget":         "Redis.hmget()",
    "hgetall":       "Redis.hgetall()",
    "lrange":        "Redis.lrange()",
    "lindex":        "Redis.lindex()",
    "smembers":      "Redis.smembers()",
    "sismember":     "Redis.sismember()",
    "zrange":        "Redis.zrange()",
    "zscore":        "Redis.zscore()",
    "exists":        "Redis.exists()",
    "ttl":           "Redis.ttl()",
}

# These Redis commands are INHERENTLY ATOMIC — safe, never flag as race
REDIS_ATOMIC_OPS: frozenset[str] = frozenset({
    "incr", "decr", "incrby", "decrby", "incrbyfloat",
    "setnx", "msetnx", "getdel", "append",
    "hincrby", "hincrbyfloat",
    "zincrby",
})


def resolve_go_method(method_name: str) -> tuple[EdgeType | None, str | None]:
    """Resolve a Go method name to an EdgeType and pattern label."""
    m = method_name.lower()
    if m in GO_ORM_WRITES:
        return EdgeType.WRITES_TO, GO_ORM_WRITES[m]
    if m in GO_ORM_READS:
        return EdgeType.READS_FROM, GO_ORM_READS[m]
    if m in GO_LOCK_PATTERNS:
        return EdgeType.USES_LOCK, GO_LOCK_PATTERNS[m]
    if m in GO_TRANSACTION_PATTERNS:
        return EdgeType.USES_TRANSACTION, GO_TRANSACTION_PATTERNS[m]
    return None, None


def resolve_redis_method(method_name: str) -> tuple[EdgeType | None, str | None]:
    """Resolve a Redis client method to an EdgeType and pattern label."""
    m = method_name.lower()
    if m in REDIS_ATOMIC_OPS:
        return None, None   # Atomic — not a race concern
    if m in REDIS_WRITES:
        return EdgeType.WRITES_TO, REDIS_WRITES[m]
    if m in REDIS_READS:
        return EdgeType.READS_FROM, REDIS_READS[m]
    return None, None

