Confirmed: the `debug_redact` sensitive-field annotation is not honored by `JsonFormat` (Java) at all — no reference to `getRedactionState()`/`DebugRedact` exists anywhere in `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`, and the Python runtime has no `redact` support whatsoever. This is a genuine, verifiable inconsistency between the schema-declared privacy annotation and one of the supported public serialization APIs.

### Title
`debug_redact = true` fields are silently printed in cleartext by `JsonFormat.Printer` - (File: java/util/src/main/java/com/google/protobuf/util/JsonFormat.java)

### Summary
Protobuf lets schema authors mark a field `[debug_redact = true]` so that any tooling built on the runtime omits the value when rendering the message for logs/diagnostics. This contract is implemented in `TextFormat`/`DebugFormat` but is completely absent from `JsonFormat`, the officially supported ProtoJSON printer. Application code that (reasonably) assumes `debug_redact` is a schema-wide guarantee and uses `JsonFormat.printer().print(message)` for logging or telemetry will leak the "sensitive" field's plaintext value, defeating the very purpose of the annotation.

### Finding Description
`debug_redact` is resolved via `FieldDescriptor.getRedactionState()` [1](#0-0)  and consumed by `TextFormat.Printer.shouldRedact()`/`printFieldValue()`, which substitute `[REDACTED]` for the value when `enablingSafeDebugFormat` is set [2](#0-1) , and similarly in the C++ `TextFormat::Printer::TryRedactFieldValue` [3](#0-2) .

`JsonFormat.Printer`, however, walks fields and serializes their raw values with no redaction check whatsoever: `printField()` dispatches straight to `printSingleFieldValue()`/`printRepeatedFieldValue()`/`printMapFieldValue()` [4](#0-3) , and the terminal `printSingleFieldValue()` switch simply emits STRING/BYTES/ENUM/MESSAGE contents unconditionally [5](#0-4) . A repo-wide search confirms `JsonFormat.java` never references `getRedactionState`, `DebugRedact`, or `debug_redact` in any form. The Python runtime's `text_format.py` and JSON code likewise contain no redaction logic at all.

The failed invariant transferred from the scikit-learn analog is the same shape: an attribute/annotation intended to prevent a specific class of data (sensitive tokens / sensitive proto fields) from being retained or exposed in a derived artifact (`stop_words_` / JSON output) is not actually enforced along all code paths that produce that artifact, so the "declared-safe" output still contains the sensitive raw value.

### Impact Explanation
Any Protobuf schema owner who marks a field `debug_redact = true` and relies on that annotation being enforced uniformly by "the protobuf redaction feature" will have those values leak whenever the message is serialized to ProtoJSON — for example if the same message type is logged via `JsonFormat` (a documented, supported API) instead of `TextFormat`/`DebugFormat`. Because `debug_redact` fields are explicitly reserved for high-sensitivity values (the C++/Java test protos use PII-like examples), this is a confidentiality violation (CWE-921/CWE-922-style improper storage/exposure of sensitive data), matching the Medium severity and disclosure-only impact profile of the CVE-2024-5206 analog (`C:H/I:N/A:N`).

### Likelihood Explanation
No attacker interaction with wire-format parsing is required to trigger the gap — it fires whenever trusted application code calls the standard `JsonFormat.printer().print()`/`print(Message)` API on a message containing a `debug_redact` field, which is a very common way teams emit structured logs or telemetry. Given that `debug_redact` is advertised as the general-purpose "field is sensitive" annotation across the C++/Java ecosystem, the likelihood that some downstream user assumes JSON printing is also covered is high, and the omission is silent (no error/warning), so it would not be caught except by explicit review of `JsonFormat.java`.

### Recommendation
Extend `JsonFormat.Printer.printSingleFieldValue()`/`printField()` (and the map/repeated variants) to consult `FieldDescriptor.getRedactionState()` the same way `TextFormat` does, replacing sensitive values with a redacted marker (or making this opt-in configurable consistent with `TextFormat`'s `enablingSafeDebugFormat`). Apply the same fix to any other language runtime's JSON printer (Python, C#, Ruby, etc.) that supports `debug_redact` in its descriptor but doesn't honor it in JSON printing, and add explicit documentation stating which printers currently honor `debug_redact` to prevent this exact misuse.

### Proof of Concept
1. Use the existing test schema `RedactedFields` which already defines `optional_redacted_string` with `[debug_redact = true]` [6](#0-5) .
2. Build a message: `RedactedFields.newBuilder().setOptionalRedactedString("SECRET_TOKEN").build()`.
3. Call `DebugFormat.multiline().toString(message)` — confirmed by existing test `messageFormat_debugRedactFieldIsRedacted` to output `optional_redacted_string: [REDACTED]` [7](#0-6) .
4. Call `JsonFormat.printer().print(message)` on the same message — because `printSingleFieldValue` has no redaction branch [8](#0-7) , the resulting JSON contains `"optionalRedactedString":"SECRET_TOKEN"` in cleartext.

I was unable to execute this PoC in a live environment (no build/test execution available here); the divergence is established purely from static code/text inspection of `TextFormat`/`DebugFormat` vs. `JsonFormat`, and from the absence of any redaction-related symbol in `JsonFormat.java` or the Python runtime confirmed via repository-wide search.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/Descriptors.java (L2085-2111)
```java
    // Lazily calculates the redact state of the field, caching the result.
    RedactionState getRedactionState() {
      RedactionState state = redactionState;
      if (state == null) {
        // If the field is directly marked with debug_redact=true, then it is sensitive.
        synchronized (this) {
          state = redactionState;
          if (state == null) {
            FieldOptions options = getOptions();
            state = RedactionState.of(options.getDebugRedact());
            // Check if the FieldOptions contain any enums that are marked as debug_redact=true,
            // either directly or indirectly via a message option.
            for (Map.Entry<Descriptors.FieldDescriptor, Object> entry :
                options.getAllFields().entrySet()) {
              state =
                  RedactionState.combine(
                      state, isOptionSensitive(entry.getKey(), entry.getValue()));
              if (state.redact) {
                break;
              }
            }
            redactionState = state;
          }
        }
      }
      return state;
    }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L668-676)
```java
    // The criteria for redacting a field is as follows: 1) The enablingSafeDebugFormat printer
    // option must be on. 2) The field must be considered "sensitive". A sensitive field can be
    // marked as sensitive via two methods: a) via a direct debug_redact=true annotation on the
    // field, b) via an enum field marked with debug_redact=true that is within the proto's
    // FieldOptions, either directly or indirectly via a message option.
    private boolean shouldRedact(final FieldDescriptor field, TextGenerator generator) {
      FieldDescriptor.RedactionState state = field.getRedactionState();
      return enablingSafeDebugFormat && state.redact;
    }
```

**File:** src/google/protobuf/text_format.cc (L3279-3305)
```text
bool TextFormat::Printer::TryRedactFieldValue(
    const Message& message, const FieldDescriptor* field,
    BaseTextGenerator* generator, bool insert_value_separator) const {
  TextFormat::RedactionState redaction_state =
      DescriptorPool::MemoizeProjection(
          field, [](const FieldDescriptor* field) {
            return TextFormat::GetRedactionState(field);
          });
  if (redaction_state.redact) {
    if (redact_debug_string_) {
      IncrementRedactedFieldCounter();
      if (insert_value_separator) {
        generator->PrintMaybeWithMarker(MarkerToken(), ": ");
      }
      generator->PrintString(kFieldValueReplacement);
      if (insert_value_separator) {
        if (single_line_mode_) {
          generator->PrintLiteral(" ");
        } else {
          generator->PrintLiteral("\n");
        }
      }
      return true;
    }
  }
  return false;
}
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1345-1360)
```java
    private void printField(FieldDescriptor field, Object value) throws IOException {
      if (field.isExtension() && printingFullyQualifiedExtensionNames) {
        generator.print("\"[" + field.getFullName() + "]\":" + blankOrSpace);
      } else if (preservingProtoFieldNames) {
        generator.print("\"" + field.getName() + "\":" + blankOrSpace);
      } else {
        generator.print("\"" + field.getJsonName() + "\":" + blankOrSpace);
      }
      if (field.isMapField()) {
        printMapFieldValue(field, value);
      } else if (field.isRepeated()) {
        printRepeatedFieldValue(field, value);
      } else {
        printSingleFieldValue(field, value);
      }
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1523-1566)
```java

        case STRING:
          printStringEscapedAndQuoted((String) value);
          break;

        case BYTES:
          generator.print("\"");
          generator.print(Base64.getEncoder().encodeToString(((ByteString) value).toByteArray()));
          generator.print("\"");
          break;

        case ENUM:
          // Special-case google.protobuf.NullValue (it's an Enum).
          if (field.getEnumType().getFullName().equals("google.protobuf.NullValue")) {
            // No matter what value it contains, we always print it as "null".
            if (alwaysWithQuotes) {
              generator.print("\"");
            }
            generator.print("null");
            if (alwaysWithQuotes) {
              generator.print("\"");
            }
          } else {
            if (printingEnumsAsInts || ((EnumValueDescriptor) value).getIndex() == -1) {
              generator.print(String.valueOf(((EnumValueDescriptor) value).getNumber()));
            } else {
              EnumValueDescriptor enumValue = (EnumValueDescriptor) value;
              JsonEnumValueOptions ext =
                  enumValue.getOptions().getExtension(JsonEnumvalueOptionsProto.json);
              if (ext.hasString()) {
                printStringEscapedAndQuoted(ext.getString());
              } else {
                generator.print("\"" + enumValue.getName() + "\"");
              }
            }
          }
          break;

        case MESSAGE:
        case GROUP:
          print((Message) value);
          break;
      }
    }
```

**File:** src/google/protobuf/unittest.proto (L2464-2467)
```text
message RedactedFields {
  string optional_redacted_string = 1 [
    debug_redact = true
  ];
```

**File:** java/core/src/test/java/com/google/protobuf/DebugFormatTest.java (L45-55)
```java
  @Test
  public void messageFormat_debugRedactFieldIsRedacted() {
    RedactedFields message = RedactedFields.newBuilder().setOptionalRedactedString("foo").build();

    String result = DebugFormat.multiline().toString(message);

    assertThat(result)
        .matches(
            String.format(
                "%soptional_redacted_string: %s\n", UNSTABLE_PREFIX_MULTILINE, REDACTED_REGEX));
  }
```
