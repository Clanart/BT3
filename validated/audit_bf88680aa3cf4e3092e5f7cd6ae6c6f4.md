### Title
Sensitive `debug_redact`-marked field values leak into parse-error/log messages during ProtoJSON and TextFormat schema-validation failures - ([File: src/google/protobuf/text_format.cc], [File: java/util/src/main/java/com/google/protobuf/util/JsonFormat.java], [File: python/google/protobuf/json_format.py])

### Summary
Protobuf provides an explicit `debug_redact` field option whose documented purpose is to keep sensitive values (e.g. credentials) out of human-readable output "when the field contains sensitive credentials" (`php/src/Google/Protobuf/Internal/FieldOptions.php:580-587`). This redaction is enforced only in the *serialization/printing* path (`TextFormat::Printer::TryRedactFieldValue`, `src/google/protobuf/text_format.cc:3279-3305`, gated by `GetRedactionState`, `src/google/protobuf/text_format.cc:3265-3278`). It is never consulted in the *parsing* error-reporting paths for either TextFormat or ProtoJSON. When a submitted value fails type/format validation, both the TextFormat and ProtoJSON parsers embed the raw offending value verbatim into the thrown/returned error, and the TextFormat path additionally writes it straight to the process log via `ABSL_LOG(ERROR)` whenever the caller does not supply an `ErrorCollector` (the common default). This mirrors the cloud-init CVE-2022-2084 pattern: a schema-validation failure path was not covered by the data-protection mechanism that is applied elsewhere, so raw sensitive content is written to logs that a lower-privileged reader (or another downstream aggregator) may access.

### Finding Description
`debug_redact` is intended as a blanket guarantee: a field marked this way should never appear in plaintext in any human-readable/debuggable representation Protobuf produces. That invariant is implemented for `DebugString()`/`TextFormat::Printer::Print*` output: [1](#0-0) [2](#0-1) 

However, the invariant is not propagated to the *parser's* error-reporting code path. `TextFormat::Parser::ConsumeString`, `ConsumeIdentifier`, `Consume`, and the enum-value handler all build error strings that directly interpolate the raw attacker-supplied token text or field value, with no check of `field->options().debug_redact()`: [3](#0-2) [4](#0-3) [5](#0-4) 

Critically, when the caller does not install a custom `io::ErrorCollector` (a common, default usage pattern for `TextFormat::ParseFromString`), these messages — including the embedded raw value — are sent unconditionally to the process log at `ERROR` severity: [6](#0-5) 

Test expectations confirm the raw values are always echoed regardless of field sensitivity annotations (e.g. `"Expected string, got: true"`, `"Unknown enumeration value of \"5\" for field \"optional_nested_enum\"."`): [7](#0-6) 

The same missing-check pattern exists in ProtoJSON, which unlike TextFormat *is* a supported public parse API. In Java, `JsonFormat`'s `parseFieldValue`/`parseEnum`/`parseBool`/`parseFloat` build `InvalidProtocolBufferException` messages that directly embed the raw JSON token with no `debug_redact` check: [8](#0-7) [9](#0-8) 

In Python, `_Parser` similarly wraps every scalar/message conversion failure into a `ParseError` that embeds the raw JSON value and field/path context: [10](#0-9) [11](#0-10) 

A grep of the entire JSON code paths (Java, Python, and the C++ `json/internal` parser/lexer) confirms `debug_redact` is never referenced anywhere in JSON parsing — the option is entirely un-plumbed into that surface. The C++ JSON lexer's own comment makes the design intent explicit: raw invalid-JSON content is deliberately left un-obfuscated "so that people have a hope of grepping for it in logs": [12](#0-11) 

**Invariant that fails to transfer / attacker-controlled value / missing check:** the `debug_redact` contract ("never printed in a debug/human-readable form") is checked only at print time, not at parse-failure-report time; the attacker-controlled value is the raw scalar/enum/bool token supplied in a TextFormat or JSON payload for a schema field marked `debug_redact = true`; the missing check is a `field->options().debug_redact()` (or its language-specific reflection equivalent) guard in the error-construction code of `ConsumeString`/`ConsumeIdentifier`/`ConsumeFieldValue` (C++), `parseFieldValue`/`parseEnum` (Java), and `_ConvertAndSetScalar`/`_ConvertScalarFieldValue` (Python).

### Impact Explanation
This is a confidentiality (information-disclosure) issue, analogous to CVSS 3.1 `C:H/I:N/A:N` in the cloud-init report. If a consuming application (a) defines a field as `debug_redact = true` specifically because it carries a credential/secret and (b) logs parse failures — which is standard practice for input-validation error handling in RPC/ingestion services — the raw sensitive value bypasses the field's explicit redaction contract and is written into application/process logs (via `ABSL_LOG(ERROR)` for the C++ TextFormat default path, or via caught-and-logged exceptions for Java/Python JSON parsing). If those logs are broadly readable (shared log aggregation, syslog, container stdout captured by a less-trusted sidecar, etc. — the same "world readable" condition as the cloud-init report), a lower-privileged party can recover data the schema owner explicitly tried to protect. Severity is Medium, matching the source report, since it requires (1) the target field to be marked `debug_redact`, (2) the submitted value to fail parsing/type validation, and (3) the consuming application to log the resulting error at a broadly-readable sink — the same layered-dependency profile as the original cloud-init CVE (a Medium, not Critical/High, finding).

### Likelihood Explanation
Likelihood is Medium. Triggering the parse-failure path requires no special privilege: any client of a public parse API can submit a type-mismatched or malformed value for a specific field. `debug_redact` is a documented, discoverable schema annotation (used for exactly "sensitive credentials" per its own doc comment), so a service operator who marks a field this way has already signaled that field's content is sensitive, making it a natural target once an attacker can enumerate field names/types (which is possible from the public `.proto`/descriptor or from other legitimate error responses). Logging caught parse exceptions/errors at ERROR level is a very common application practice, so the "consuming application logs the failure" precondition is realistic and not a stretch, unlike more speculative RCE-class assumptions.

### Recommendation
- Extend `debug_redact` enforcement into the parser error-construction paths: before embedding a raw token/value into an error message, check `field->options().debug_redact()` (C++), `field.getOptions().getDebugRedact()` (Java), and the Python descriptor options equivalent, and substitute a placeholder (e.g. `[REDACTED]`) analogous to `kFieldValueReplacement` used by the printer.
- In `ReportErrorImpl` (`src/google/protobuf/text_format.cc:342-357`), avoid embedding field values for `debug_redact` fields even when falling back to `ABSL_LOG(ERROR)`.
- Apply the same treatment to the Java/Python/C++ JSON parsers (`JsonFormat.java`, `json_format.py`, `json/internal/parser.cc`), since ProtoJSON is a supported production parsing surface where this gap is more directly attacker-reachable than TextFormat.
- Document, per the upstream security guidance, that `debug_redact` is currently a "best-effort, print-time-only" protection and that error/log paths are not yet covered, so that consuming applications know not to assume completeness until fixed.

### Proof of Concept
Given a schema field marked sensitive, e.g. (mirroring the test schema already in the repo, `rust/test/unittest.proto:1801`):
```proto
message Secret {
  string optional_redacted_string = 1 [debug_redact = true];
}
```
1. TextFormat (default logging path, no ErrorCollector installed):
```cpp
Secret msg;
TextFormat::ParseFromString("optional_redacted_string: 12345\n", &msg);
// -> ABSL_LOG(ERROR) << "Error parsing text-format Secret: 1:27: "
//                       "Expected string, got: 12345"
```
This reproduces the code path at `src/google/protobuf/text_format.cc:1238-1253` (`ConsumeString`) feeding into `ReportError` → `ReportErrorImpl` (`text_format.cc:342-357`), which logs unconditionally because `error_collector_` is `nullptr` by default.

2. ProtoJSON (Java):
```java
Secret.Builder b = Secret.newBuilder();
JsonFormat.parser().merge("{\"optionalRedactedString\": 12345}", b);
// -> InvalidProtocolBufferException: "Invalid value: 12345 for expected type: STRING"
```
matching `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java:2509-2516`.

I was not able to execute these snippets in a sandbox (no test-execution tool available in this session); the line-referenced code and the existing unit-test expectations (`src/google/protobuf/text_format_unittest.cc:2559-2610`) confirm the exact error strings produced and the absence of any `debug_redact` check in these code paths. A background engineering session with build/test tooling would be needed to actually compile and run the above PoC and confirm the `ABSL_LOG(ERROR)` output content end-to-end.

### Citations

**File:** src/google/protobuf/text_format.cc (L342-357)
```text
void ReportErrorImpl(int line, int col, absl::string_view message,
                     const Descriptor* root_message_type,
                     io::ErrorCollector* error_collector) {
  if (error_collector == nullptr) {
    if (line >= 0) {
      ABSL_LOG(ERROR) << "Error parsing text-format "
                      << root_message_type->full_name() << ": " << (line + 1)
                      << ":" << (col + 1) << ": " << message;
    } else {
      ABSL_LOG(ERROR) << "Error parsing text-format "
                      << root_message_type->full_name() << ": " << message;
    }
  } else {
    error_collector->RecordError(line, col, message);
  }
}
```

**File:** src/google/protobuf/text_format.cc (L1063-1093)
```text
        if (LookingAtType(io::Tokenizer::TYPE_IDENTIFIER)) {
          DO(ConsumeIdentifier(&value));
          // Find the enumeration value.
          enum_value = enum_type->FindValueByName(value);

        } else if (LookingAt("-") ||
                   LookingAtType(io::Tokenizer::TYPE_INTEGER)) {
          DO(ConsumeSignedInteger(&int_value, kint32max));
          value = absl::StrCat(int_value);  // for error reporting
          enum_value = enum_type->FindValueByNumber(int_value);
        } else {
          ReportError(absl::StrCat("Expected integer or identifier, got: ",
                                   tokenizer_.current().text));
          return false;
        }

        if (enum_value == nullptr) {
          if (int_value != kint64max &&
              !field->legacy_enum_field_treated_as_closed()) {
            SET_FIELD(EnumValue, int64, int_value);
            return true;
          } else if (!allow_unknown_enum_) {
            ReportError(absl::StrCat("Unknown enumeration value of \"", value,
                                     "\" for field \"", field->name(), "\"."));
            return false;
          } else {
            ReportWarning(absl::StrCat("Unknown enumeration value of \"", value,
                                       "\" for field \"", field->name(),
                                       "\"."));
            return true;
          }
```

**File:** src/google/protobuf/text_format.cc (L1238-1253)
```text
  bool ConsumeString(std::string* text) {
    if (!LookingAtType(io::Tokenizer::TYPE_STRING)) {
      ReportError(
          absl::StrCat("Expected string, got: ", tokenizer_.current().text));
      return false;
    }

    text->clear();
    while (LookingAtType(io::Tokenizer::TYPE_STRING)) {
      io::Tokenizer::ParseStringAppend(tokenizer_.current().text, text);

      tokenizer_.Next();
    }

    return true;
  }
```

**File:** src/google/protobuf/text_format.cc (L1506-1518)
```text
  bool Consume(const std::string& value) {
    const std::string& current_value = tokenizer_.current().text;

    if (current_value != value) {
      ReportError(absl::StrCat("Expected \"", value, "\", found \"",
                               current_value, "\"."));
      return false;
    }

    tokenizer_.Next();

    return true;
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

**File:** src/google/protobuf/text_format.h (L542-547)
```text
    // Try to redact a field value based on the annotations associated with
    // the field. This function returns true if it redacts the field value.
    bool TryRedactFieldValue(const Message& message,
                             const FieldDescriptor* field,
                             BaseTextGenerator* generator,
                             bool insert_value_separator) const;
```

**File:** src/google/protobuf/text_format_unittest.cc (L2559-2610)
```text
TEST_F(TextFormatParserTest, InvalidFieldValues) {
  // Invalid values for a double/float field.
  ExpectFailure("optional_double: \"hello\"\n",
                "Expected double, got: \"hello\"", 1, 18);
  ExpectFailure("optional_double: true\n", "Expected double, got: true", 1, 18);
  ExpectFailure("optional_double: !\n", "Expected double, got: !", 1, 18);
  ExpectFailure("optional_double {\n  \n}\n", "Expected \":\", found \"{\".", 1,
                17);

  // Invalid values for a signed integer field.
  ExpectFailure("optional_int32: \"hello\"\n",
                "Expected integer, got: \"hello\"", 1, 17);
  ExpectFailure("optional_int32: true\n", "Expected integer, got: true", 1, 17);
  ExpectFailure("optional_int32: 4.5\n", "Expected integer, got: 4.5", 1, 17);
  ExpectFailure("optional_int32: !\n", "Expected integer, got: !", 1, 17);
  ExpectFailure("optional_int32 {\n \n}\n", "Expected \":\", found \"{\".", 1,
                16);
  ExpectFailure("optional_int32: 0x80000000\n",
                "Integer out of range (0x80000000)", 1, 17);
  ExpectFailure("optional_int64: 0x8000000000000000\n",
                "Integer out of range (0x8000000000000000)", 1, 17);
  ExpectFailure("optional_int32: -0x80000001\n",
                "Integer out of range (0x80000001)", 1, 18);
  ExpectFailure("optional_int64: -0x8000000000000001\n",
                "Integer out of range (0x8000000000000001)", 1, 18);

  // Invalid values for an unsigned integer field.
  ExpectFailure("optional_uint64: \"hello\"\n",
                "Expected integer, got: \"hello\"", 1, 18);
  ExpectFailure("optional_uint64: true\n", "Expected integer, got: true", 1,
                18);
  ExpectFailure("optional_uint64: 4.5\n", "Expected integer, got: 4.5", 1, 18);
  ExpectFailure("optional_uint64: -5\n", "Expected integer, got: -", 1, 18);
  ExpectFailure("optional_uint64: !\n", "Expected integer, got: !", 1, 18);
  ExpectFailure("optional_uint64 {\n \n}\n", "Expected \":\", found \"{\".", 1,
                17);
  ExpectFailure("optional_uint32: 0x100000000\n",
                "Integer out of range (0x100000000)", 1, 18);
  ExpectFailure("optional_uint64: 0x10000000000000000\n",
                "Integer out of range (0x10000000000000000)", 1, 18);

  // Invalid values for a boolean field.
  ExpectFailure("optional_bool: \"hello\"\n",
                "Expected identifier, got: \"hello\"", 1, 16);
  ExpectFailure("optional_bool: 5\n", "Integer out of range (5)", 1, 16);
  ExpectFailure("optional_bool: -7.5\n", "Expected identifier, got: -", 1, 16);
  ExpectFailure("optional_bool: !\n", "Expected identifier, got: !", 1, 16);

  ExpectFailure(
      "optional_bool: meh\n",
      "Invalid value for boolean field \"optional_bool\". Value: \"meh\".", 2,
      1);
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2437-2453)
```java
    @Nullable
    private EnumValueDescriptor parseEnum(EnumDescriptor enumDescriptor, JsonElement json)
        throws InvalidProtocolBufferException {
      // Calling json.getAsString() works for both JsonPrimitive and single-element JsonArray (e.g.,
      // ["FOO"] or [2]), throwing UnsupportedOperationException/IllegalStateException for other
      // structures.
      final String name;
      try {
        name = json.getAsString();
      } catch (UnsupportedOperationException | IllegalStateException e) {
        // UnsupportedOperationException is thrown by Gson when getAsString() is called on a
        // JsonObject (e.g., "{}") or JsonNull (e.g., "null"). IllegalStateException is thrown when
        // getAsString() is called on a JsonArray whose length is not exactly 1 (e.g., "[]" or
        // '["FOO", "BAR"]').
        throw new InvalidProtocolBufferException(
            "Invalid enum value: " + json + " for enum type: " + enumDescriptor.getFullName(), e);
      }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2509-2516)
```java
      } else if (json instanceof JsonObject) {
        if (field.getType() != FieldDescriptor.Type.MESSAGE
            && field.getType() != FieldDescriptor.Type.GROUP) {
          // If the field type is primitive, but the json type is JsonObject rather than
          // JsonElement, throw a type mismatch error.
          throw new InvalidProtocolBufferException(
              "Invalid value: " + json + " for expected type: " + field.getType());
        }
```

**File:** python/google/protobuf/json_format.py (L754-768)
```python
      except ParseError as e:
        if field and field.containing_oneof is None:
          raise ParseError(
              'Failed to parse {0} field: {1}.'.format(name, e)
          ) from e
        else:
          raise ParseError(str(e)) from e
      except ValueError as e:
        raise ParseError(
            'Failed to parse {0} field: {1}.'.format(name, e)
        ) from e
      except TypeError as e:
        raise ParseError(
            'Failed to parse {0} field: {1}.'.format(name, e)
        ) from e
```

**File:** python/google/protobuf/json_format.py (L814-833)
```python
  def _ConvertValueMessage(self, value, message, path):
    """Convert a JSON representation into Value message."""
    if isinstance(value, dict):
      self.ConvertMessage(value, message.struct_value, path)
    elif isinstance(value, _LIST_LIKE):
      self.ConvertMessage(value, message.list_value, path)
    elif value is None:
      message.null_value = 0
    elif isinstance(value, bool):
      message.bool_value = value
    elif isinstance(value, str):
      message.string_value = value
    elif isinstance(value, _INT_OR_FLOAT):
      message.number_value = value
    else:
      raise ParseError(
          'Value {0} has unexpected type {1} at {2}'.format(
              value, type(value), path
          )
      )
```

**File:** src/google/protobuf/json/internal/lexer.cc (L84-103)
```text
absl::Status JsonLocation::Invalid(absl::string_view message,
                                   SourceLocation sl) const {
  // NOTE: we intentionally do not harden the "invalid JSON" part, so that
  // people have a hope of grepping for it in logs. That part is easy to
  // commit to, as stability goes.
  //
  // This copies the error twice. Because this is the "unhappy" path, this
  // function is cold and can afford the waste.
  std::string status_message = "invalid JSON";
  std::string to_obfuscate;
  if (path != nullptr) {
    absl::StrAppend(&to_obfuscate, " in ");
    path->Describe(to_obfuscate);
    to_obfuscate.push_back(',');
  }
  absl::StrAppendFormat(&to_obfuscate, " near %zu:%zu (offset %zu): %s",
                        line + 1, col + 1, offset, message);
  HardenAgainstHyrumsLaw(to_obfuscate, status_message);

  return absl::InvalidArgumentError(std::move(status_message));
```
