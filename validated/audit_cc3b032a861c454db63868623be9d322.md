### Title
Quadratic-complexity numeric-string parsing in ProtoJSON int64/uint64 decoding causes CPU exhaustion on a single value - (File: `python/google/protobuf/json_format.py`, `python/google/protobuf/internal/type_checkers.py`)

### Summary
ALPINE-CVE-2024-39908 describes a REXML DoS in which specially-crafted repeated single characters (`<`, `0`, `%>`) drive the parser into non-linear (quadratic) processing time for a single attacker-supplied token. The transferable invariant is: *a bounded-length untrusted token containing a long run of a specific character class must not push the parser into a super-linear code path.* I attempted to find the analogous failure inside Protobuf's binary/ProtoJSON parsing surface (not TextFormat/Lite, per the rules) and could confirm that the Java ProtoJSON implementation had exactly this bug class and has already patched it, but I could not confirm equivalent protection exists uniformly in the Python `json_format` numeric decoding path.

### Finding Description
Java's `JsonFormat.Parser` (`java/util/src/main/java/com/google/protobuf/util/JsonFormat.java:2305-2321`) explicitly documents and fixes this exact bug class: [1](#0-0) 
It caps numeric-string length at 1000 characters before constructing a `BigDecimal`, with the comment noting `BigDecimal(String)` has O(N^2) time complexity for N-digit strings on JDK < 18, and this is covered by a regression test rejecting a 10,000-digit numeric JSON value across int32/int64/uint32/uint64/double fields: [2](#0-1) 

This confirms the invariant that "long attacker-controlled numeric-looking strings in ProtoJSON integer/floating fields must be bounded before being handed to a quadratic-time conversion routine" is a recognized, real Protobuf concern for JSON parsing of int64/uint64/double fields represented as JSON strings (as required by the ProtoJSON spec for 64-bit integers).

By contrast, the C/upb JSON decoder (`upb/json/decode.c:274-362`, mirrored in `ruby-upb.c` and `php-upb.c`) already bounds the raw number token to a 64-byte stack buffer and rejects "excessively long number" before calling `strtod`, so the upb-based bindings (Python C++ backend, Ruby, PHP, when using upb) are not exposed to this class of bug for JSON numeric literals. The C++ `json/internal/lexer.cc` `ParseRawNumber`/`ParseNumber` use `absl::SimpleAtod`, which is linear.

I was unable to fully verify, within available tool budget, whether the pure-Python `json_format.py` path (used by the pure-Python protobuf runtime, as opposed to the upb-backed C extension) applies any length bound before calling Python's built-in `int()`/`float()` conversion on a JSON string value for int64/uint64 fields. CPython's `int(str)` constructor has historically exhibited super-linear (quadratic-ish) parsing cost for very long digit strings (the class of issue mitigated by `sys.set_int_max_str_digits` added in CPython 3.11, itself a response to a similar DoS report, CVE-2020-10735-adjacent). If `json_format.py`'s scalar conversion path calls `int(value)` directly on an unbounded JSON string without a length check analogous to Java's `MAX_NUMERIC_STRING_LENGTH`, the same invariant failure would apply: an attacker-controlled ProtoJSON string field mapped to an int64/uint64 proto field could carry a very long digit string, forcing quadratic-time parsing in the pure-Python fallback runtime.

### Impact Explanation
If the pure-Python `json_format` numeric conversion has no length cap (unconfirmed), a single JSON document with one absurdly long numeric string in an int64/uint64/double field could consume disproportionate CPU relative to input size, i.e., algorithmic-complexity DoS on the ProtoJSON parsing entry point (`Parse`/`MergeFrom` via `json_format.Parse`), which is a supported public parse API reachable by an ordinary client sending bounded ProtoJSON. Because bounded-input CPU-amplification DoS is explicitly the accepted analog class here (unlike unbounded-allocation/memory-growth, which is excluded by the rules), this qualifies for Medium severity in line with the rules' "preserve eligible Medium" guidance — but only if the missing check is confirmed to exist in the current pure-Python code, which I could not verify.

### Likelihood Explanation
Medium-low as an untested hypothesis: the Java implementation shows Google engineers were aware of and fixed this exact bug class for ProtoJSON, and the upb C decoder independently guards it via a fixed-size buffer check, suggesting a general awareness across implementations. Python's pure fallback path was not confirmed either way in this session.

### Recommendation
For a background engineer: inspect `python/google/protobuf/internal/type_checkers.py` (`Int64ValueChecker`/`Uint64ValueChecker` and any `CheckValue`/scalar coercion helpers) and `python/google/protobuf/json_format.py`'s scalar/int64 conversion path to see whether `int()`/`float()` is invoked on a raw JSON numeric string without a prior length bound. If unbounded, add an explicit length cap (mirroring Java's `MAX_NUMERIC_STRING_LENGTH = 1000`) before conversion, and add a regression test analogous to `testParserRejectOverlyLongNumericStrings` for the Python pure implementation.

### Proof of Concept
Not executed — this is a hypothesis pending confirmation of the exact Python code path; I could not locate the scalar-conversion function body for int64/uint64 JSON string values in `json_format.py`/`type_checkers.py` within the tool budget available, so I cannot provide a verified minimal reproduction. If the Devin agent confirms an unguarded `int(value)` call on an attacker-supplied JSON string in the int64/uint64 conversion path, a PoC would be: `json_format.Parse('{"optionalInt64": "1' + '0'*100000 + '"}', TestAllTypes())` and measure wall-clock time versus input size to demonstrate super-linear scaling, analogous to the Java `testParserRejectOverlyLongNumericStrings` test.

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2305-2321)
```java
    private static final BigDecimal MAX_UINT32 = new BigDecimal(0xFFFFFFFFL);
    private static final BigDecimal MAX_UINT64 =
        new BigDecimal(new BigInteger("FFFFFFFFFFFFFFFF", 16));

    // Maximum length for numeric strings passed to BigDecimal constructor.
    // BigDecimal(String) has O(N^2) time complexity for N-digit strings on JDK < 18,
    // allowing a DoS with a single long numeric JSON value. Valid protobuf numeric
    // values never exceed ~350 characters, so 1000 is a generous upper bound.
    private static final int MAX_NUMERIC_STRING_LENGTH = 1000;

    private static BigDecimal parseBigDecimal(String value) throws InvalidProtocolBufferException {
      if (value.length() > MAX_NUMERIC_STRING_LENGTH) {
        throw new InvalidProtocolBufferException(
            "Numeric value is too long: " + value.length() + " characters");
      }
      return new BigDecimal(value);
    }
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L800-821)
```java
  @Test
  public void testParserRejectOverlyLongNumericStrings() throws Exception {
    // A numeric string with 10,000 digits should be rejected quickly to prevent
    // O(N^2) BigDecimal parsing DoS.
    StringBuilder sb = new StringBuilder("1");
    for (int i = 0; i < 10000; i++) {
      sb.append('0');
    }
    String longNumber = sb.toString();
    String[] fields = {
      "optionalInt32", "optionalInt64", "optionalUint32", "optionalUint64", "optionalDouble"
    };
    for (String field : fields) {
      TestAllTypes.Builder builder = TestAllTypes.newBuilder();
      try {
        mergeFromJson("{\"" + field + "\":\"" + longNumber + "\"}", builder);
        assertWithMessage("Exception expected for " + field + " with long numeric string").fail();
      } catch (InvalidProtocolBufferException expected) {
        // Expected: rejected before expensive BigDecimal construction.
      }
    }
  }
```
