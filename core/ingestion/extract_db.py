    def _extract_db_identity(self, env_vars: dict, fallback_id: str) -> str:
        if not isinstance(env_vars, dict):
            # Might be a list like ["DATABASE_URL=postgres://db"]
            if isinstance(env_vars, list):
                env_dict = {}
                for item in env_vars:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        env_dict[k] = v
                env_vars = env_dict
            else:
                return fallback_id

        # 1. Explicit override
        if "OMNIGRAPH_DB_IDENTITY" in env_vars:
            return f"db_{env_vars['OMNIGRAPH_DB_IDENTITY']}"

        # 2. Extract from standard variables
        for key, val in env_vars.items():
            k = key.upper()
            if "DATABASE_URL" in k or "DB_HOST" in k or "POSTGRES_HOST" in k:
                val = str(val).lower()
                # Extremely simplified host extraction for prototype
                host = val
                if "://" in val:
                    # postgres://user:pass@hostname:port/db
                    parts = val.split("://")[1]
                    if "@" in parts:
                        parts = parts.split("@")[1]
                    host = parts.split(":")[0].split("/")[0]
                return f"db_{host}"

        # 3. Fallback
        return fallback_id
