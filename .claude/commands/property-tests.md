---
description: Generate property-based tests from a problem statement BEFORE implementation. Attacks underspecification at the source, especially for high-stakes code (compliance, medical, financial).
---

Generate Hypothesis-library property tests from this problem statement:

$ARGUMENTS

## Why property tests FIRST

Example-based tests check known inputs. Property tests encode invariants that must hold for ALL inputs. For high-stakes code — HIPAA, finance, correctness-critical logic — example tests miss the edge cases that matter. Property tests force the implementation to satisfy the actual requirement, not just the examples.

## Protocol

### Step 1: Extract invariants
From the problem statement, list all properties that must ALWAYS hold:
- Input → output relationships (monotonicity, commutativity, idempotence)
- Boundary conditions (min, max, empty, null, negative, zero, infinity)
- Safety properties (never returns invalid state, never loses data)
- Security properties (never leaks auth, never bypasses validation)
- Compliance properties (from CLAUDE.md — HIPAA, PCI, SOC2 rules)

### Step 2: Translate to Hypothesis strategies
For each input type, define a `hypothesis.strategies` generator:

```python
from hypothesis import given, strategies as st, settings, assume
from hypothesis.strategies import composite

@composite
def valid_patient_record(draw):
    return {
        "id": draw(st.uuids()),
        "dob": draw(st.dates(min_value=date(1900,1,1), max_value=date.today())),
        "ssn": draw(st.text(alphabet="0123456789", min_size=9, max_size=9)),
        ...
    }
```

### Step 3: Write property tests
For each invariant, one test:

```python
@given(record=valid_patient_record())
@settings(max_examples=200, deadline=None)
def test_phi_never_appears_in_logs(record, caplog):
    process_patient(record)
    # INVARIANT: SSN never logged, even on error paths
    for log_record in caplog.records:
        assert record["ssn"] not in log_record.message
        assert "ssn" not in log_record.message.lower()

@given(amount=st.decimals(min_value=0, max_value=1_000_000, places=2))
def test_monetary_roundtrip_is_lossless(amount):
    # INVARIANT: encoding/decoding money never loses precision
    assert decode(encode(amount)) == amount
```

### Step 4: Verify tests FAIL against empty implementation
1. Write a stub that returns `NotImplementedError` or dummy values
2. Run the tests
3. ALL must fail — if any pass, the property is wrong or the stub is too smart

### Step 5: Hand off to implementation
The coder now has a hard specification. The implementation isn't "done" until:
- All property tests pass
- `hypothesis --statistics` shows >1000 examples explored per test
- No shrunk failing example exists

## Rules
- Use `hypothesis` for Python (for JS use `fast-check`, for Go use `gopter`)
- Include `@settings(max_examples=200)` minimum for serious invariants
- Always use `@composite` for related fields (don't generate invalid combos)
- Every property test must have a one-line comment explaining WHICH invariant it guards
- Never use only example-based tests for security, privacy, or correctness-critical code

## When to use this command
✅ Medical logic (PHI handling, clinical calculations)
✅ Financial code (money arithmetic, rounding, currency)
✅ Auth and authz boundaries
✅ Data transformations that must be lossless
✅ Any function where "almost correct" means "wrong"

❌ Simple CRUD
❌ UI glue code
❌ Hot paths where generating 200 examples per test is too slow

## Output
1. Test file at the conventional location
2. Properties listed at top as comments
3. Hypothesis strategies for every input type
4. Stub implementation that makes all tests fail
5. A note in `docs/plans/` describing the property contract
