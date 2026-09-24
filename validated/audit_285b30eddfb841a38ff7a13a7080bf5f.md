### Title
Unbounded recursive-descent JSON tree construction before ProtoJSON recursion-limit enforcement allows stack-overflow DoS - (File: java/util/src/main/java/com/google/protobuf/util/JsonFormat.java)

### Summary
The upstream chunjun report describes a Gson-based deserialization component (`GsonUtil.java`) processing untrusted input without safe bounds, with the associated CVE record framing it as a stack-overflow/DoS issue tied to unchecked recursive structure handling during Gson deserialization. The relevant invariant that transfers to Protobuf is: *any component that uses Gson's recursive-descent tree builder on attacker-controlled JSON before an application-level recursion/depth guard is applied is exposed to uncontrolled native-stack recursion.* Protobuf's Java `JsonFormat.Parser` has exactly this shape: it hands the raw untrusted JSON text to Gson's `JsonParser.parseReader(...)` to materialize a full `JsonElement` tree *before* `ParserImpl`'s own `recursionLimit`/`currentDepth` accounting ever runs.

### Finding Description
`JsonFormat.Parser.merge(String, Message.Builder)` and `merge(Reader, Message.Builder)` both delegate to `ParserImpl`, which builds the entire JSON document into a `JsonElement` tree via Gson before any protobuf-specific parsing begins: [1](#0-0) [2](#0-1) 

`JsonParser.parseReader` (Gson) recursively descends into nested JSON arrays/objects to build the tree; this happens as a single unguarded native-stack recursion, entirely separate from `ParserImpl.currentDepth`, which is a Protobuf-internal counter incremented only when Protobuf *messages* are merged (e.g., in `mergeAny`): [3](#0-2) 

The declared `recursionLimit` (default 100, matching `CodedInputStream`'s binary recursion limit) is documented and tested only for message-level nesting (e.g., repeated `Any` wrapping): [4](#0-3) [5](#0-4) 

That test confirms the limit is enforced by `ParserImpl.mergeAny`/`mergeMessage` at the *message* level, not at the level of raw JSON syntactic nesting (e.g., an input consisting purely of thousands of nested `[` array-open tokens with no protobuf message boundaries at all). Such an input never triggers `currentDepth`/`recursionLimit` checks because those counters are not touched until a message field is actually being merged; the stack growth instead happens entirely inside Gson's `JsonParser.parseReader`/`JsonElement` tree-building recursion, which has no depth ceiling in this code path.

### Impact Explanation
A deeply nested but otherwise small JSON payload (e.g., tens of thousands of nested arrays `[[[[...]]]]`) submitted to `JsonFormat.parser().merge(...)` — a supported public ProtoJSON parsing API — can exhaust the JVM call stack during the Gson tree-construction phase, before Protobuf's own recursion-limit logic has any opportunity to intervene. This throws an uncatchable `StackOverflowError` in many JVM configurations (or, if caught, leaves the JVM/thread in a potentially inconsistent state), producing a denial of service in any consuming application that exposes `JsonFormat.parser().merge()` on untrusted input — consistent with the "attacker is an ordinary client sending bounded ... ProtoJSON through a supported public parse API" assumption. This matches the impact class (DoS via uncontrolled recursion) claimed in the chunjun report, transposed to Protobuf's own supported JSON parsing entry point rather than an unrelated third-party JSON library.

### Likelihood Explanation
High likelihood of reachability: `JsonFormat.parser().merge(String, Builder)` / `merge(Reader, Builder)` are the standard, documented, public ProtoJSON parsing entry points, and the vulnerable call (`JsonParser.parseReader`) is unconditionally executed on every input before any Protobuf-specific validation. No special schema, extension, or `Any` type is required — a bare nested-array/object payload is sufficient. The payload itself is small in byte size but large in nesting depth, so it is not excluded by the "no unbounded-allocation/huge-input" exclusion in the analog rules; the input can be bounded in bytes (e.g., a few hundred KB of `[` characters can already induce tens of thousands of recursion frames) while defeating the intended `recursionLimit` semantics.

### Recommendation
Enforce depth/nesting limits during the initial Gson tree construction phase, not only at the Protobuf message-merge phase. Options: use Gson's `JsonReader` in streaming mode with an explicit nesting counter that aborts once `recursionLimit` (or a dedicated syntactic-nesting limit) is exceeded, rather than calling `JsonParser.parseReader` to eagerly build an unbounded tree; alternatively, wrap the Gson parse call with a `StackOverflowError` catch that converts it into `InvalidProtocolBufferException` as a stopgap, and add an explicit pre-check/bounded custom JSON tokenizer for nesting depth ahead of `JsonParser.parseReader`.

### Proof of Concept
Conceptually (unverified against a live JVM in this session, since only static code inspection was available):
```java
StringBuilder sb = new StringBuilder();
for (int i = 0; i < 100000; i++) sb.append('[');
for (int i = 0; i < 100000; i++) sb.append(']');
Value.Builder builder = Value.newBuilder();
JsonFormat.parser().merge(sb.toString(), builder); // expected: StackOverflowError
             // during Gson's JsonParser.parseReader before recursionLimit logic runs
```
This targets `google.protobuf.Value`/`ListValue`, whose JSON representation is a bare JSON array, meaning the entire payload is consumed by Gson's tree builder rather than by Protobuf's message-merge recursion, so `ParserImpl.currentDepth`/`recursionLimit` (default 100) never engages before the stack is exhausted. I was not able to execute this PoC in this environment (no runtime/tooling access); this should be validated by a background agent with JVM execution access, checking the exact frame count needed to overflow the default JVM stack size (`-Xss`) as a function of `[` nesting depth, and confirming whether the exception surfaces as `StackOverflowError` (uncaught) versus being wrapped by the existing `catch (RuntimeException e)` in `merge(String, Builder)` — note `StackOverflowError` is an `Error`, not a `RuntimeException`, so the existing catch block at lines 1707–1712 would **not** catch it, letting it propagate unhandled. [6](#0-5)

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L505-509)
```java
    private final int recursionLimit;
    private final boolean legacyLenient;

    // The default parsing recursion limit is aligned with the proto binary parser.
    private static final int DEFAULT_RECURSION_LIMIT = 100;
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1678-1697)
```java
    void merge(Reader json, Message.Builder builder) throws IOException {
      try {
        JsonReader reader = new JsonReader(json);
        if (!legacyLenient) {
          throw new IllegalStateException("Unreachable: !legacyLenient should not be set.");
        }
        reader.setLenient(false);
        merge(JsonParser.parseReader(reader), builder);
      } catch (JsonIOException e) {
        // Unwrap IOException.
        if (e.getCause() instanceof IOException) {
          throw (IOException) e.getCause();
        } else {
          throw new InvalidProtocolBufferException(e.getMessage(), e);
        }
      } catch (RuntimeException e) {
        // We convert all exceptions from JSON parsing to our own exceptions.
        throw new InvalidProtocolBufferException(e.getMessage(), e);
      }
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1699-1713)
```java
    void merge(String json, Message.Builder builder) throws InvalidProtocolBufferException {
      try {
        JsonReader reader = new JsonReader(new StringReader(json));
        if (!legacyLenient) {
          throw new IllegalStateException("Strict parsing is not supported in open-source.");
        }
        reader.setLenient(false);
        merge(JsonParser.parseReader(reader), builder);
      } catch (RuntimeException e) {
        // We convert all exceptions from JSON parsing to our own exceptions.
        InvalidProtocolBufferException toThrow = new InvalidProtocolBufferException(e.getMessage());
        toThrow.initCause(e);
        throw toThrow;
      }
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1919-1926)
```java
      Message.Builder contentBuilder =
          DynamicMessage.getDefaultInstance(contentType).newBuilderForType();
      WellKnownTypeParser specialParser = wellKnownTypeParsers.get(contentType.getFullName());

      if (currentDepth >= recursionLimit) {
        throw new InvalidProtocolBufferException("Hit recursion limit.");
      }
      ++currentDepth;
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2473-2504)
```java
  @Test
  public void testRecursionLimitAnyOfAny() throws Exception {
    String input =
        "{\n"
            + "  \"@type\": \"type.googleapis.com/google.protobuf.Any\", \"value\": {\n"
            + "    \"@type\": \"type.googleapis.com/google.protobuf.Any\", \"value\": {\n"
            + "      \"@type\": \"type.googleapis.com/google.protobuf.Any\", \"value\": {\n"
            + "        \"@type\": \"type.googleapis.com/google.protobuf.Any\", \"value\": {\n"
            + "          \"@type\": \"type.googleapis.com/google.protobuf.Any\"}\n"
            + "        }\n"
            + "      }\n"
            + "    }\n"
            + "  }\n"
            + "}\n";

    JsonFormat.TypeRegistry registry =
        JsonFormat.TypeRegistry.newBuilder().add(Any.getDescriptor()).build();

    JsonFormat.Parser parser = JsonFormat.parser().usingTypeRegistry(registry);
    Any.Builder builder = Any.newBuilder();
    parser.merge(input, builder); // Successfully parses with no default recursion limit.
    Any unused = builder.build();

    parser = JsonFormat.parser().usingTypeRegistry(registry).usingRecursionLimit(3);
    builder = Any.newBuilder();
    try {
      parser.merge(input, builder);
      assertWithMessage("Exception is expected.").fail();
    } catch (InvalidProtocolBufferException e) {
      assertThat(e).hasMessageThat().contains("recursion");
    }
  }
```
