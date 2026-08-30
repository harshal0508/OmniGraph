import json

PROMPT_TEMPLATE = """You are an automated security arbiter evaluating potential database race conditions.

You will be provided with a structural skeleton of a codebase pattern.
Your job is to classify if this pattern represents a real Time-of-Check-to-Time-of-Use (TOCTOU) race condition (True Positive) or unrelated/safe database access (False Positive).

CRITICAL INSTRUCTIONS:
1. **Write-Write Conflicts**: If BOTH functions perform a `WRITES_TO` on the same table, this is highly likely a True Positive (e.g., concurrent operations overwriting each other). *Exception*: If the table names strongly imply an append-only log (e.g., `audit_log`), concurrent writes are safe and it is a False Positive.
2. **Read-Write Conflicts**: If one function performs `READS_FROM` and the other performs `WRITES_TO`, it is ONLY a True Positive if they are part of the *same execution flow* (a check-then-act). 
   - If `Flow Evidence` indicates a `direct_call`, they share a flow, making it a True Positive.
   - If `Flow Evidence` is `none`, do NOT guess based on function names. Instead, default to an `uncertain` verdict (meaning it requires human review). 
3. If the scenario is ambiguous or lacks sufficient evidence to prove a shared flow, output the "uncertain" verdict.

Pattern: {category}
Function 1: {f1_name} — {f1_access}({table})
Function 2: {f2_name} — {f2_access}({table})
Service: {service} (replicas: {replicas})
Lock present: {lock}
Transaction present: {transaction}
Flow Evidence: {flow_evidence}

Output your response as strict JSON matching this schema exactly:
{{
  "verdict": "true_positive" | "false_positive" | "uncertain",
  "confidence": 0.0,
  "reasoning": "one sentence, plain language explaining why",
  "severity_if_true": "low" | "medium" | "high" | "critical"
}}
"""
