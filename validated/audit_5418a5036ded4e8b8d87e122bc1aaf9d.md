### Title
Insertion of Sensitive Field Values into Application Logs via TextFormat Parse Error Reporting Bypassing `debug_redact` Protection - (File: `src/google/protobuf/text_format.cc`)

### Summary
The Ghost report describes insertion of sensitive information into log files when application data is written into logs without sanitization. Protobuf has a purpose-built mechanism, `debug_redact`, to prevent sensitive field values from appearing in printed/logged representations of a message [1](#0-0) . However, this redaction is only applied on the *printing* path (`TryRedactFieldValue`, `PrintUnknownFields`) [2](#0-1) . The *parsing* error/warning path for `TextFormat::Parser` embeds raw, attacker/caller-supplied token text directly into error messages that are, by default, written to `ABSL_LOG(ERROR)`/`ABSL_LOG(WARNING)` without any redaction check.

### Finding Description
`TextFormat::Parser::ParserImpl::ConsumeFieldValue` builds error strings that directly concatenate the raw parsed token value when a value fails to convert to the target field type, e.g. for bool fields:
```
ReportError(absl::StrCat("Invalid value for boolean field \"",
                         field->name(), "\". Value: \"", value, "\"."));
``` [3](#0-2) 
and similarly for enum fields, embedding the raw text of an unrecognized enum value into both the error and warning strings:
```
ReportError(absl::StrCat("Unknown enumeration value of \"", value,
                         "\" for field \"", field->name(), "\"."));
...
ReportWarning(absl::StrCat("Unknown enumeration value of \"", value,
                           "\" for field \"", field->name(), "\"."));
``` [4](#0-3) 

These `ReportError`/`ReportWarning` calls funnel into `ReportErrorImpl`, which — whenever the caller has not supplied a custom `io::ErrorCollector` (the default when calling `TextFormat::ParseFromString`/`Parser::Parse` without configuring one) — writes the message straight to `ABSL_LOG(ERROR)`:
```
void ReportErrorImpl(int line, int col, absl::string_view message,
                     const Descriptor* root_message_type,
                     io::ErrorCollector* error_collector) {
  if (error_collector == nullptr) {
    ...
    ABSL_LOG(ERROR) << "Error parsing text-format " ... << message;
  } else {
    error_collector->RecordError(line, col, message);
  }
}
``` [5](#0-4) 
The equivalent warning path uses `ABSL_LOG_EVERY_POW_2(WARNING)` [6](#0-5) .

The invariant that fails to transfer/hold here is the same one from the Ghost CVE: **field values must not be written unredacted into logs that the field's own annotations say should be protected**. Protobuf explicitly implements `debug_redact` field options and a `TextFormat::GetRedactionState`/`shouldRedact` mechanism precisely to prevent this class of leak in printed output [7](#0-6)  and the Java implementation has an analogous `shouldRedact` gate on its printer [8](#0-7) . That protection is scoped only to the print/serialize-to-text path; it is never consulted when the *parser* reports a malformed token, because `ConsumeFieldValue`'s error paths have no knowledge of `field->options().debug_redact()` at all — they only use `field->name()` and the raw token `value`. Consequently, an application that:
1. parses a TextFormat message that includes a field marked `debug_redact = true` (e.g. an auth token, secret, or PII-bearing enum/bool/string represented as a malformed token), and
2. does not always supply its own `io::ErrorCollector`,

will have the raw attacker/caller-controlled token value written to the process's default logging sink via `ABSL_LOG(ERROR)`/`ABSL_LOG(WARNING)`, even though the exact same field would be redacted as `[REDACTED]` if the message were instead printed via `DebugString()`/`PrintToString()`.

### Impact Explanation
This is an information-disclosure bug: sensitive field values (any field the schema author has explicitly marked `debug_redact = true` to keep out of logs/telemetry) can leak into application logs whenever a caller feeds TextFormat containing a malformed/unparseable value for such a field, defeating the specific protection mechanism protobuf ships for this exact scenario. Logs are frequently persisted, aggregated, and have broader access/retention than the original data, so this is a genuine sensitive-data-into-logs exposure, matching the class of the Ghost CVE (`BIT-ghost-2024-34559`, insertion of sensitive information into a log file).

### Likelihood Explanation
Likelihood is moderate: TextFormat is a supported public parsing API (`TextFormat::Parser::Parse`, `TextFormat::ParseFromString`, `Message::AbslParseFlagImpl`'s TextFormat branch which uses a custom collector but many other call sites in the ecosystem do not) [9](#0-8) . Any consuming application that parses partially-trusted TextFormat (config files, debug endpoints, flag values, RPC debug fields) without wiring a custom `ErrorCollector` will hit the default `ABSL_LOG` path whenever the input contains a syntactically-invalid bool/enum value for a redacted field — this requires only a bounded, malformed token, not a large or malicious payload. It does not apply to binary or ProtoJSON parsing (those paths were checked and do not embed raw field values into `ABSL_LOG` calls; the JSON parser uses `absl::Status` messages returned to the caller, and binary parse-failure logging in `message_lite.cc` only ever logs field *names* via `InitializationErrorMessage`, not values [10](#0-9) ), so the exposure is specific to the TextFormat surface.

### Recommendation
In `TextFormat::Parser::ParserImpl::ConsumeFieldValue`/`SkipFieldValue` error and warning constructors, consult the field's redaction state (`TextFormat::GetRedactionState(field)`) before embedding the raw token `value` into the message, substituting a placeholder (consistent with `kFieldValueReplacement` used on the print path) when `debug_redact` is set. Apply the same treatment to the Java `TextFormat` parser's error/warning paths for parity.

### Proof of Concept
Given a `.proto` schema with:
```proto
enum Status { OK = 0; SECRET = 1 [(debug_redact) = true]; }
message M { Status s = 1; }
```
Calling the default parser (no custom `ErrorCollector`) with TextFormat input:
```
s: "not_a_real_enum_value_containing_secret_token_XYZ"
```
via `TextFormat::ParseFromString(input, &m)` reaches `ConsumeFieldValue`'s enum branch [11](#0-10) , fails `FindValueByName`, and (assuming `allow_unknown_enum_` is unset, the default) calls `ReportError` with the full raw string embedded, which is then written verbatim to `ABSL_LOG(ERROR)` by `ReportErrorImpl` [5](#0-4) . I was not able to execute this in a live build within this session (no code-execution access), so this is a static-analysis-confirmed code path, not an executed/verified test run; a background agent with a build environment should compile and run this exact scenario with `absl::ScopedMockLog` (as already used in `message_unittest.inc` for a related logging assertion pattern [12](#0-11) ) to capture and confirm the logged output contains the unredacted token.

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

**File:** src/google/protobuf/text_format.cc (L479-494)
```text
  void ReportWarning(int line, int col, const absl::string_view message) {
    if (error_collector_ == nullptr) {
      if (line >= 0) {
        ABSL_LOG_EVERY_POW_2(WARNING)
            << "Warning parsing text-format " << root_message_type_->full_name()
            << ": " << (line + 1) << ":" << (col + 1) << " (N = " << COUNTER
            << "): " << message;
      } else {
        ABSL_LOG_EVERY_POW_2(WARNING)
            << "Warning parsing text-format " << root_message_type_->full_name()
            << " (N = " << COUNTER << "): " << message;
      }
    } else {
      error_collector_->RecordWarning(line, col, message);
    }
  }
```

**File:** src/google/protobuf/text_format.cc (L1043-1051)
```text
          if (value == "true" || value == "True" || value == "t") {
            SET_FIELD(Bool, bool, true);
          } else if (value == "false" || value == "False" || value == "f") {
            SET_FIELD(Bool, bool, false);
          } else {
            ReportError(absl::StrCat("Invalid value for boolean field \"",
                                     field->name(), "\". Value: \"", value,
                                     "\"."));
            return false;
```

**File:** src/google/protobuf/text_format.cc (L1057-1093)
```text
      case FieldDescriptor::CPPTYPE_ENUM: {
        std::string value;
        int64_t int_value = kint64max;
        const EnumDescriptor* enum_type = field->enum_type();
        const EnumValueDescriptor* enum_value = nullptr;

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

**File:** src/google/protobuf/text_format.cc (L3164-3182)
```text
          if (redact_debug_string_) {
            generator->PrintMaybeWithMarker(MarkerToken(), ": ",
                                            "UNKNOWN_STRING ");
            OutOfLinePrintString(generator, kFieldValueReplacement);
            if (single_line_mode_) {
              generator->PrintLiteral(" ");
            } else {
              generator->PrintLiteral("\n");
            }
            break;
          }
          generator->PrintMaybeWithMarker(MarkerToken(), ": ", "\"");
          generator->PrintString(absl::CEscape(value));
          if (single_line_mode_) {
            generator->PrintLiteral("\" ");
          } else {
            generator->PrintLiteral("\"\n");
          }
        }
```

**File:** src/google/protobuf/text_format.cc (L3265-3278)
```text
TextFormat::RedactionState TextFormat::GetRedactionState(
    const FieldDescriptor* field) {
  auto options = field->options();
  auto state = TextFormat::RedactionState{options.debug_redact(), false};
  std::vector<const FieldDescriptor*> field_options;
  const Reflection* reflection = options.GetReflection();
  reflection->ListFields(options, &field_options);
  for (const FieldDescriptor* option : field_options) {
    auto result = TextFormat::IsOptionSensitive(options, reflection, option);
    state = TextFormat::RedactionState{state.redact || result.redact,
                                       state.report || result.report};
  }
  return state;
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

**File:** src/google/protobuf/message.cc (L282-303)
```text
  switch (header.format) {
    case AbslFlagFormat::kTextFormat: {
      static constexpr absl::string_view kIgnoreUnknown = "ignore_unknown";
      if (!verify_options({kIgnoreUnknown, kBase64})) return false;
      if (!unescape_if_needed()) return false;
      TextFormat::Parser parser;
      struct StringErrorCollector : io::ErrorCollector {
        explicit StringErrorCollector(std::string& error) : error(error) {}
        std::string& error;
        void RecordError(int line, io::ColumnNumber column,
                         absl::string_view message) override {
          error = absl::StrFormat("(Line %v, Column %v): %v", line, column,
                                  message);
        }
      } collector(error);
      if (absl::c_linear_search(header.options, kIgnoreUnknown)) {
        parser.AllowUnknownField(true);
        parser.AllowUnknownExtension(true);
      }
      parser.RecordErrorsTo(&collector);
      return parser.ParseFromString(text, this);
    }
```

**File:** src/google/protobuf/message_lite.cc (L166-172)
```text
std::string InitializationErrorMessage(absl::string_view action,
                                       const MessageLite& message) {
  return absl::StrCat("Can't ", action, " message of type \"",
                      message.GetTypeName(),
                      "\" because it is missing required fields: ",
                      message.InitializationErrorString());
}
```

**File:** src/google/protobuf/message_unittest.inc (L331-341)
```text
  {
    absl::ScopedMockLog log(absl::MockLogDefault::kDisallowUnexpected);
    EXPECT_CALL(log, Log(absl::LogSeverity::kError, testing::_,
                         absl::StrCat("Can't parse message of type \"",
                                      UNITTEST_PACKAGE_NAME,
                                      ".TestRequired\" because it is missing "
                                      "required fields: a, b, c")));
    log.StartCapturingLogs();
    EXPECT_FALSE(message.ParseFromString(""));
  }
}
```
