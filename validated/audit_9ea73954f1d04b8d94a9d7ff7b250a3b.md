### Title
Unbounded recursion during `Any`-to-JSON printing/serialization lacks a depth guard (stack-overflow DoS) - (File: `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`)

### Summary
The protobufjs advisory describes a DoS where converting an already-decoded message containing deeply nested `google.protobuf.Any` values to JSON (`toObject()`, `toJSON()`, `JSON.stringify()`) recurses without a depth limit and exhausts the JS call stack. The failed invariant is: *conversion of a decoded message to JSON must be bounded by the same recursion limit that protects parsing.* Searching this checkout shows that invariant has been carefully restored for the **parsing direction** (JSON text → message) in every implementation examined (C#, Java, Python, C++/`json/internal`, upb), each with explicit `recursionLimit`/`currentDepth`/`max_recursion_depth` checks and regression tests specifically citing "Any in Any" recursion bugs. However, the **serialization direction** (decoded message → JSON, i.e. `Printer.print()` / `MessageToJson()` / `WriteAny()` / `jsonenc_any()`), which is exactly the code path the advisory targets, shows no equivalent depth counter in the code paths I could inspect.

### Finding Description
`Any.value` is stored as an opaque `bytes` field, so a chain of `Any` wrapping `Any` wrapping `Any` … N levels deep is trivially shallow from the point of view of the *outer* binary parse (each level is just a `bytes` field), so it easily passes any top-level `CodedInputStream` recursion limit. The deep nesting is only realized lazily, when the printer recursively decodes and re-prints each inner `Any`.

In Java, `JsonFormat.util`'s parser (`mergeAny`, `JsonFormat.java:1885-1937`) explicitly guards this exact scenario: [1](#0-0) 
and there is a dedicated regression test, `testRecursionLimitAnyOfAny`, added specifically to prevent stack overflow from Any-of-Any nesting: [2](#0-1) 

But the corresponding **printer** method, `printAny` (`JsonFormat.java:1032-1077`), which decodes the `Any.value` bytes into a `DynamicMessage` and recurses via `print(contentMessage, typeUrl)` or `printer.print(this, contentMessage)`, contains no `currentDepth`/`recursionLimit` check whatsoever: [3](#0-2) 
The `PrinterImpl` class fields (`registry`, `oldRegistry`, `extensionRegistry`, formatting flags) contain no depth counter analogous to `ParserImpl.currentDepth`/`recursionLimit`: [4](#0-3) 

The same asymmetry appears in the other backends surveyed:
- Python's `_Parser` tracks `self.recursion_depth`/`self.max_recursion_depth` (`json_format.py:535-589`), but the `_Printer._AnyMessageToJsonObject` (`json_format.py:356-376`) recursively calls `_RegularMessageToJsonObject` with no depth counter in `_Printer`.
- C++'s JSON parser (`ParseAny`, `parser.cc:1030-1089`) explicitly notes "Copying `lex.options()` is important; it inherits the recursion limit," but `WriteAny`/`WriteMessage` in `unparser.cc` had zero matches for "depth" in the file, i.e. no recursion counter guarding message printing.
- upb's `jsondec_push` in the JSON *decoder* (`upb/json/decode.c:228-233`) decrements `d->depth` and errors on underflow, but the JSON *encoder* `jsonenc_any`/`jsonenc_msgfield` (`upb/json/encode.c:390-424`) had only a single unrelated match for "depth" in the whole file — no depth counter guarding the recursive `Any` expansion during encoding.

### Impact Explanation
An application that decodes attacker-influenced protobuf binary data containing a chain of nested `google.protobuf.Any` messages (each individually a tiny, well-formed, shallow message — trivially passing the binary parser's recursion limit) and then converts the decoded message to JSON via `JsonFormat.printer().print()`, `MessageToJson()`, `MessageToJsonString`, or the upb JSON encoder, can trigger uncontrolled recursion proportional to the attacker-chosen `Any` nesting depth. This can exhaust the call stack and crash the serving process — a process-level denial of service (CWE-674), matching the CVSS `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H` profile of the original advisory.

### Likelihood Explanation
High for any application that (a) accepts protobuf binary from an untrusted client, (b) has `google.protobuf.Any` in its schema with a type registry that can resolve the attacker-chosen type URLs, and (c) converts the decoded message to JSON (a very common pattern for logging, REST gateways, debugging endpoints, or JSON-based APIs layered on protobuf). Because the nesting depth is encoded entirely inside a `bytes` field, the malicious payload is small and easy to construct, and it bypasses the binary-parse recursion limit entirely — the vulnerability is only reachable through the print/serialize path, not the parse/merge path, which is consistent with the advisory's own scoping ("applications that only decode and re-encode ... without converting to JSON are not directly affected").

### Recommendation
Add an explicit recursion/depth counter to the printer/encoder-side `Any` expansion, mirroring the guard already present on the parser side:
- Java: add a `currentDepth`/`recursionLimit` field to `PrinterImpl` and check it in `printAny` before recursing into `print(contentMessage, ...)`.
- Python: add a `recursion_depth`/`max_recursion_depth` counter to `_Printer`, incremented/checked in `_MessageToJsonObject`/`_AnyMessageToJsonObject`.
- C++: add depth tracking to `JsonWriter`/`WriteAny` in `unparser.cc`, consistent with the `JsonLexer` depth tracking already present in `parser.cc`.
- upb: add a depth counter to `jsonenc` mirroring `jsondec`'s `d->depth` check, checked in `jsonenc_any`.

### Proof of Concept
Not independently executed in this environment (no filesystem/terminal access in ask-only mode). Conceptually reproducible as follows for the Java path (analogous constructions apply to Python/C++/upb):
1. Construct `Any` messages `A0 = Any{}`; for `i` in `1..N`: `A[i] = Any.pack(A[i-1])` (type_url `type.googleapis.com/google.protobuf.Any`), producing a binary blob whose *serialized nesting* is N Any-wrapping-Any layers, but whose outer `CodedInputStream` parse depth is 1 (each layer is just a `bytes` field).
2. Register `Any.getDescriptor()` in a `JsonFormat.TypeRegistry`.
3. Call `JsonFormat.printer().usingTypeRegistry(registry).print(A[N])` with large N (e.g. tens of thousands) — each level triggers a fresh `DynamicMessage.parseFrom` + recursive `printAny` call with no depth check, unlike `mergeAny`'s guarded parse path (`JsonFormat.java:1923-1926`), which would reject the equivalent JSON input via `testRecursionLimitAnyOfAny` (`JsonFormatTest.java:2473-2504`). This is expected to overflow the JVM stack rather than raise a catchable `InvalidProtocolBufferException`, mirroring the protobufjs advisory's exact failure mode. This claim is based on static code inspection only; no test execution was performed against the actual checkout in this session.

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L877-927)
```java
  /** A Printer converts protobuf messages to the ProtoJSON format. */
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

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1032-1077)
```java
    private void printAny(MessageOrBuilder message) throws IOException {
      if (message.getDefaultInstanceForType().equals(message)) {
        generator.print("{}");
        return;
      }
      Descriptor descriptor = message.getDescriptorForType();
      FieldDescriptor typeUrlField = descriptor.findFieldByName("type_url");
      FieldDescriptor valueField = descriptor.findFieldByName("value");
      // Validates type of the message. Note that we can't just cast the message
      // to com.google.protobuf.Any because it might be a DynamicMessage.
      if (typeUrlField == null
          || valueField == null
          || typeUrlField.getType() != FieldDescriptor.Type.STRING
          || valueField.getType() != FieldDescriptor.Type.BYTES) {
        throw new InvalidProtocolBufferException("Invalid Any type.");
      }
      String typeUrl = (String) message.getField(typeUrlField);
      Descriptor type = registry.getDescriptorForTypeUrl(typeUrl);
      if (type == null) {
        type = oldRegistry.getDescriptorForTypeUrl(typeUrl);
        if (type == null) {
          throw new InvalidProtocolBufferException("Cannot find type for url: " + typeUrl);
        }
      }
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
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1923-1926)
```java
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
