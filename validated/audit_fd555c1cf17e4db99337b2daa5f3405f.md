## Analysis: Analog Found — Unbounded Recursion in `JsonFormat.Printer` (Java)

The Jettison bug (`CVE-2023-1436`) failed because the JSON *encoder* recursed into nested elements with no depth check, so a bounded-looking input could still drive unbounded call-stack growth and cause a `StackOverflowError`. The transferable invariant is: **any code path that walks a message tree to serialize it to JSON must enforce the same depth bound that the corresponding parser enforces** — otherwise the parser's protection is one-sided and printing/serialization remains exploitable.

In this checkout, that invariant is enforced consistently on the **parsing** side (Java `JsonFormat.Parser` at [1](#0-0) , C# `JsonParser.Merge` at [2](#0-1) , Python `_Parser.ConvertMessage` at [3](#0-2) , upb's `jsondec_push` at [4](#0-3) , and C++ `CodedInputStream`/`ParseContext` depth counters at [5](#0-4) ), but **not on the ProtoJSON printing side** for Java.

### Title
Unbounded recursion in `JsonFormat.Printer` when printing deeply-nested `Any` messages — (File: `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`)

### Summary
`JsonFormat.printer().print()`/`appendTo()` recursively descends into embedded messages (including `google.protobuf.Any` payloads) with no recursion-depth limit, unlike `JsonFormat.parser()`, which tracks `currentDepth`/`recursionLimit` explicitly. A bounded, attacker-supplied binary `Any` message that nests `Any`-in-`Any` thousands of times can be parsed successfully (parsing `Any` itself is non-recursive — it is just a `string`/`bytes` pair) and then crash the consuming application with a `StackOverflowError` the moment it is passed to the public `JsonFormat.printer().print()` API.

### Finding Description
`PrinterImpl` (the class backing `JsonFormat.Printer`) has no depth-tracking field at all, in contrast to `ParserImpl`, which explicitly maintains `currentDepth` and `recursionLimit` fields: [1](#0-0) . `PrinterImpl` only carries formatting state (`registry`, `generator`, etc.): [6](#0-5) .

The recursive chain is:
- `printAny()` decodes the `Any.value` bytes into a `DynamicMessage` and, for non-well-known types, calls `print(contentMessage, typeUrl)` recursively: [7](#0-6) .
- `print(MessageOrBuilder, String)` iterates fields and calls `printField`, which for `MESSAGE`/`GROUP` fields calls `print((Message) value)` again: [8](#0-7) .
- Each level of nested `Any` re-enters `printAny()` via the well-known-type dispatch table, with no counter ever consulted or incremented: [9](#0-8) .

Critically, **parsing the outer `Any` message from bytes does not trigger the binary `CodedInputStream` recursion limit**, because `Any` itself is a flat two-field message (`string type_url`, `bytes value`); the "nested Any" only becomes a real message once `DynamicMessage...parseFrom(content, ...)` is called *inside* `printAny`, one level at a time, each such call independently satisfying the (per-call) 100-depth binary limit trivially. Thus the binary depth check protects each individual decode step but the printer's cross-level Java call stack is never checked at all — this is exactly the Jettison failure mode: a check exists somewhere in the pipeline, but not on the serialization code path, so bounded input still causes unbounded native recursion.

### Impact Explanation
Any consuming application that logs, debug-prints, or forwards untrusted `Any`-typed protobuf messages through `JsonFormat.printer().print()`/`.appendTo()` (a very common pattern for gRPC/REST gateways, audit logging, and debugging tooling) can be crashed with an uncatchable `StackOverflowError` from a single, modestly sized attacker-supplied message. This matches CWE-674 (uncontrolled recursion) and results in availability impact (process/thread crash), consistent with the High severity of the Jettison analog (CVSS `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`).

### Likelihood Explanation
High. The attacker needs only send a bounded binary or already-parsed `Any` message with deep `Any`-in-`Any` nesting (each level costs roughly the fixed overhead of a `type_url` string plus tag/length bytes, so a few thousand nesting levels fit comfortably in a message of a few hundred KB — well under any total-size limit) through any public API that eventually formats the message as ProtoJSON. No privileged access or malicious schema/plugin is required — only the standard, publicly documented `Any.parseFrom()` followed by `JsonFormat.printer().print()`.

### Recommendation
Add an explicit recursion/depth counter to `PrinterImpl` (mirroring `ParserImpl.currentDepth`/`recursionLimit`) that is incremented before, and decremented after, each recursive call into `print()`/`printAny()`/`printField()` for message-typed values, throwing a catchable `InvalidProtocolBufferException` once the configured limit (e.g., matching `Parser.DEFAULT_RECURSION_LIMIT = 100`) is exceeded, exactly as already done for the parser side: [10](#0-9) .

### Proof of Concept
Conceptually (not run in this environment — a background Devin session would be needed to execute and confirm):
1. Build a deeply nested binary `Any` programmatically: `Any inner = Any.newBuilder().setTypeUrl("type.googleapis.com/google.protobuf.Any").setValue(bytes).build()`, repeated N ≈ 5,000–10,000 times, each iteration wrapping the previous serialized `Any` as the `value` of a new outer `Any`.
2. Confirm `Any.parseFrom(serializedBytes)` succeeds without error (since parsing only decodes the top-level flat `Any`, no recursion limit triggers).
3. Call `JsonFormat.printer().print(any)` (or `.appendTo(any, writer)`).
4. Observe a `StackOverflowError` originating from the `printAny → print → printField → print` cycle, with no `InvalidProtocolBufferException` ever thrown, unlike the equivalent parser-side test `testRecursionLimitAnyOfAny` which does bound this on the parse path: [11](#0-10) .

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L508-509)
```java
    // The default parsing recursion limit is aligned with the proto binary parser.
    private static final int DEFAULT_RECURSION_LIMIT = 100;
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L878-927)
```java
  private static final class PrinterImpl {
    private final com.google.protobuf.TypeRegistry registry;
    private final TypeRegistry oldRegistry;
    private final ExtensionRegistry extensionRegistry;
    private final ShouldPrintDefaults shouldPrintDefaults;
    private final Set<FieldDescriptor> includingDefaultValueFields;
    private final boolean preservingProtoFieldNames;
    private final boolean printingEnumsAsInts;
    private final boolean sortingMapKeys;
    private final boolean printingFullyQualifiedExtensionNames;
    private final TextGenerator generator;
    // We use Gson to help handle string escapes.
    private final Gson gson;
    private final CharSequence blankOrSpace;

    private static class GsonHolder {
      private static final Gson DEFAULT_GSON = new GsonBuilder().create();
    }

    PrinterImpl(
        com.google.protobuf.TypeRegistry registry,
        TypeRegistry oldRegistry,
        ExtensionRegistry extensionRegistry,
        ShouldPrintDefaults shouldPrintDefaults,
        Set<FieldDescriptor> includingDefaultValueFields,
        boolean preservingProtoFieldNames,
        Appendable jsonOutput,
        boolean omittingInsignificantWhitespace,
        boolean printingEnumsAsInts,
        boolean sortingMapKeys,
        boolean printingFullyQualifiedExtensionNames) {
      this.registry = registry;
      this.oldRegistry = oldRegistry;
      this.extensionRegistry = extensionRegistry;
      this.shouldPrintDefaults = shouldPrintDefaults;
      this.includingDefaultValueFields = includingDefaultValueFields;
      this.preservingProtoFieldNames = preservingProtoFieldNames;
      this.printingEnumsAsInts = printingEnumsAsInts;
      this.sortingMapKeys = sortingMapKeys;
      this.printingFullyQualifiedExtensionNames = printingFullyQualifiedExtensionNames;
      this.gson = GsonHolder.DEFAULT_GSON;
      // json format related properties, determined by printerType
      if (omittingInsignificantWhitespace) {
        this.generator = new CompactTextGenerator(jsonOutput);
        this.blankOrSpace = "";
      } else {
        this.generator = new PrettyTextGenerator(jsonOutput);
        this.blankOrSpace = " ";
      }
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L948-956)
```java
      // Special-case Any.
      printers.put(
          Any.getDescriptor().getFullName(),
          new WellKnownTypePrinter() {
            @Override
            public void print(PrinterImpl printer, MessageOrBuilder message) throws IOException {
              printer.printAny(message);
            }
          });
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1056-1076)
```java
      ByteString content = (ByteString) message.getField(valueField);
      Message contentMessage =
          DynamicMessage.getDefaultInstance(type)
              .getParserForType()
              .parseFrom(content, extensionRegistry);
      WellKnownTypePrinter printer = wellKnownTypePrinters.get(getTypeName(typeUrl));
      if (printer != null) {
        // If the type is one of the well-known types, we use a special
        // formatting.
        generator.println("{");
        generator.indent();
        generator.println("\"@type\":" + blankOrSpace + gson.toJson(typeUrl) + ",");
        generator.print("\"value\":" + blankOrSpace);
        printer.print(this, contentMessage);
        generator.println();
        generator.outdent();
        generator.print("}");
      } else {
        // Print the content message instead (with a "@type" field added).
        print(contentMessage, typeUrl);
      }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1561-1564)
```java
        case MESSAGE:
        case GROUP:
          print((Message) value);
          break;
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1656-1676)
```java
    private final int recursionLimit;
    private final boolean legacyLenient;
    private int currentDepth;
    private final Map<EnumDescriptor, AlternativeEnumJsonNames> enumJsonNamesCache =
        new HashMap<>();

    ParserImpl(
        com.google.protobuf.TypeRegistry registry,
        TypeRegistry oldRegistry,
        ExtensionRegistry extensionRegistry,
        boolean ignoreUnknownFields,
        int recursionLimit,
        boolean legacyLenient) {
      this.registry = registry;
      this.oldRegistry = oldRegistry;
      this.extensionRegistry = extensionRegistry;
      this.ignoringUnknownFields = ignoreUnknownFields;
      this.recursionLimit = recursionLimit;
      this.legacyLenient = legacyLenient;
      this.currentDepth = 0;
    }
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L132-146)
```csharp
        private void Merge(IMessage message, JsonTokenizer tokenizer)
        {
            if (tokenizer.RecursionDepth > settings.RecursionLimit)
            {
                throw InvalidProtocolBufferException.JsonRecursionLimitExceeded();
            }
            tokenizer.RecursionDepth++;

            // try/finally used in order to decrement the recursion depth regardless of outcome.
            // If an exception is thrown, the recursion depth is irrelevant anyway - but as the method
            // has multiple return statements, this is the simplest way of ensuring the recursion depth
            // is always decremented. An alternative would be to use a local function.
            try
            {
                if (message.Descriptor.IsWellKnownType)
```

**File:** python/google/protobuf/json_format.py (L568-578)
```python
    # Increment recursion depth at message entry. The max_recursion_depth limit
    # is exclusive: a depth value equal to max_recursion_depth will trigger an
    # error. For example, with max_recursion_depth=5, nesting up to depth 4 is
    # allowed, but attempting depth 5 raises ParseError.
    self.recursion_depth += 1
    if self.recursion_depth > self.max_recursion_depth:
      raise ParseError(
          'Message too deep. Max recursion depth is {0}'.format(
              self.max_recursion_depth
          )
      )
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L3912-3917)
```c
static void jsondec_push(jsondec* d) {
  if (--d->depth < 0) {
    jsondec_err(d, "Recursion limit exceeded");
  }
  d->is_first = true;
}
```

**File:** src/google/protobuf/parse_context.h (L836-845)
```text
  // part of the parse state.
  // Current depth of the active parser, depth counts down.
  // This is used to limit recursion depth (to prevent overflow on malicious
  // data), but is also used to index in stack_ to store the current state.
  int depth_;
  // Unfortunately necessary for the fringe case of ending on 0 or end-group tag
  // in the last kSlopBytes of a ZeroCopyInputStream chunk. Note that INT16_MIN
  // is intentionally used to avoid decrementing INT_MIN, which is UB.
  int group_depth_ = std::numeric_limits<int16_t>::min();
  Data data_;
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
