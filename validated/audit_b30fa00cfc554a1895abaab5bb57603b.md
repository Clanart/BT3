### Title
Pure-Python `text_format` module implements no `debug_redact` enforcement, silently disclosing fields marked sensitive when the C++ backend is unavailable - ([File: python/google/protobuf/text_format.py])

### Summary
The external report concerns a Mattermost plugin failing to mask sensitive configuration values when configuration is exported (e.g., into support packets), disclosing secrets an operator explicitly marked as sensitive. Protobuf's direct analog to "fields marked sensitive must not appear in exported/debug output" is the `debug_redact` field option, enforced by `TextFormat`'s "safe debug format" (`DebugFormat` in Java, `SetRedactDebugString`/`TryRedactFieldValue` in C++) [1](#0-0) . This mechanism exists specifically to keep credentials/PII out of debug strings that get copied into logs, bug reports, and support artifacts [2](#0-1) . The C++ and Java implementations correctly gate output on the field's redaction state [3](#0-2) [4](#0-3) , but the pure-Python implementation of `text_format.py` (used whenever the native/upb accelerator is unavailable, e.g., pure-Python installs) contains **no reference at all** to `debug_redact` or any redaction concept — confirmed by exhaustive search of `python/**` and all `*.py` files in the checkout.

### Finding Description
`debug_redact` is a `FieldOptions` extension that authors set on schema fields carrying sensitive values (credentials, tokens, PII) so that any debug/text rendering of the message masks them as `[REDACTED]` instead of the raw value [1](#0-0) . The invariant "a field with `debug_redact=true` must never surface its plaintext value through the safe-debug/TextFormat surface" is enforced in the C++ core via `TextFormat::Printer::TryRedactFieldValue`, which computes `RedactionState` per field and substitutes `kFieldValueReplacement` ("[REDACTED]") whenever `redact_debug_string_` is enabled [2](#0-1) , and in Java via `TextFormat.Printer.shouldRedact()` / `DebugFormat` which consult `FieldDescriptor.getRedactionState()` [3](#0-2) [5](#0-4) .

The pure-Python `_Printer.PrintMessage`/`PrintField` path in `python/google/protobuf/text_format.py` walks `message.ListFields()` and prints every field's value unconditionally, including via `_TryPrintAsAnyMessage` for `Any`-typed fields, with no check against `FieldOptions.debug_redact` anywhere in the file [6](#0-5) [7](#0-6) . There is no `RedactionState`, no `[REDACTED]` marker string, and no `enablingSafeDebugFormat`/`redact_debug_string_` equivalent flag in this module (confirmed by grepping the entire `python/**` tree and all `.py` files for `redact`/`debug_redact`, which returned zero matches). Consequently, any application relying on Python protobuf's `text_format.MessageToString()` (or `str(message)`/`repr(message)`, which route through the same printer) to produce a "safe" debug/log/support-packet representation of a message will emit the full plaintext of fields the schema author explicitly annotated as sensitive.

### Impact Explanation
This is a direct, exploitable analog of CWE-200 for the same reason as the original report: an application-level invariant ("this field's value is sensitive and must be masked before being exposed in exported/debug data") is defined by trusted schema authors via `debug_redact = true`, but one first-party supported implementation of the enforcement point silently drops the check. Any consuming application that builds diagnostic/support exports, structured logs, or crash reports from Python protobuf `text_format` output (a documented, intended purpose of `debug_redact`) will leak secrets it believed were protected, exactly mirroring the Mattermost plugin's failure to mask sensitive configuration values in exported/support-packet data. Because the omission is silent (no exception, no warning, output looks well-formed), the disclosure is not fail-closed and would not be caught by ordinary functional testing that doesn't specifically assert redaction. Severity is High under CWE-200/disclosure-via-exposed-data-in-support-tooling, matching the CVSS profile of the source advisory (`AC:L`, `PR:H`/none depending on caller, `C:H`).

### Likelihood Explanation
Likelihood is high in any deployment where the C++/upb accelerator is not present (pure-Python fallback, some minimal or sandboxed environments, certain WASM/embedded uses, or environments where `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` is forced), and where application code uses `google.protobuf.text_format` (or implicit `str()`/logging of messages) as the mechanism for producing debug/support output for schemas that use `debug_redact`. No attacker interaction on the wire is required beyond the trusted application choosing to log/export a message containing sensitive fields — the bug is a missing safety check in the redaction feature itself, not a parsing exploit, so the "ordinary client sending bounded input" framing translates into "any legitimately populated message with a `debug_redact` field is mishandled by this code path."

### Recommendation
Port the `debug_redact` enforcement logic (per-field `RedactionState`, recursive check of enum/message-typed `FieldOptions` for indirect sensitivity, and `[REDACTED]` substitution) from the C++ (`text_format.cc`, `TryRedactFieldValue`/`GetRedactionState`/`IsOptionSensitive`) or Java (`Descriptors.FieldDescriptor.getRedactionState`) implementations into `python/google/protobuf/text_format.py`, and expose an explicit "safe debug format" printer option consistent with the other language implementations so pure-Python and accelerated backends behave identically. Add regression tests analogous to `unittest_redaction.proto`/`DebugFormatTest`/`text_format_unittest.cc` for the Python module.

### Proof of Concept
1. Define a proto message with a `debug_redact = true` string field (as in `src/google/protobuf/unittest_redaction.proto`, e.g. `optional_redacted_string`) [8](#0-7) .
2. Force the pure-Python implementation (`PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`), populate the redacted field with a secret value, and call `google.protobuf.text_format.MessageToString(message)`.
3. Observe the printer walks `ListFields()`/`PrintField` unconditionally with no redaction check [9](#0-8) , so the output contains the plaintext secret (e.g., `optional_redacted_string: "supersecret"`) rather than `[REDACTED]`, whereas the equivalent C++/Java `DebugFormat`/safe-debug printer would emit `optional_redacted_string: [REDACTED]` per the passing tests in `DebugFormatTest.java` and `text_format_unittest.cc` [10](#0-9) .

Note: I could not find a Python-side test file exercising `debug_redact`/`text_format` redaction to confirm whether this gap is already tracked or intentionally out of scope (e.g., pure-Python may be considered unsupported/legacy for this feature); this should be verified against upstream issue tracker/roadmap before filing, since the index does not show a corresponding Python security doc or CHANGELOG entry addressing it.

### Citations

**File:** src/google/protobuf/descriptor.proto (L776-778)
```text
  // Indicate that the field value should not be printed out when using debug
  // formats, e.g. when the field contains sensitive credentials.
  optional bool debug_redact = 16 [default = false];
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

**File:** java/core/src/main/java/com/google/protobuf/DebugFormat.java (L9-41)
```java
public final class DebugFormat {

  private final boolean isSingleLine;

  private TextFormat.Printer getPrinter() {
    // This assumes that `debugFormatPrinter()` is multi-line by default.
    TextFormat.Printer printer = TextFormat.debugFormatPrinter();
    if (isSingleLine) {
      return printer.emittingSingleLine(true);
    } else {
      return printer;
    }
  }

  private DebugFormat(boolean singleLine) {
    isSingleLine = singleLine;
  }

  public static DebugFormat singleLine() {
    return new DebugFormat(true);
  }

  public static DebugFormat multiline() {
    return new DebugFormat(false);
  }

  public String toString(MessageOrBuilder message) {
    TextFormat.Printer.FieldReporterLevel fieldReporterLevel =
        isSingleLine
            ? TextFormat.Printer.FieldReporterLevel.DEBUG_SINGLE_LINE
            : TextFormat.Printer.FieldReporterLevel.DEBUG_MULTILINE;
    return getPrinter().printToString(message, fieldReporterLevel);
  }
```

**File:** python/google/protobuf/text_format.py (L429-444)
```python
  def _TryPrintAsAnyMessage(self, message):
    """Serializes if message is a google.protobuf.Any field."""
    if '/' not in message.type_url:
      return False
    packed_message = _BuildMessageFromTypeName(
        message.TypeName(), self.descriptor_pool
    )
    if packed_message is not None:
      packed_message.MergeFromString(message.value)
      colon = ':' if self.force_colon else ''
      self.out.write('%s[%s]%s ' % (self.indent * ' ', message.type_url, colon))
      self._PrintMessageFieldValue(packed_message)
      self.out.write(' ' if self.as_one_line else '\n')
      return True
    else:
      return False
```

**File:** python/google/protobuf/text_format.py (L457-498)
```python
  def PrintMessage(self, message):
    """Convert protobuf message to text format.

    Args:
      message: The protocol buffers message.
    """
    if self.message_formatter and self._TryCustomFormatMessage(message):
      return
    if (
        message.DESCRIPTOR.full_name == _ANY_FULL_TYPE_NAME
        and self._TryPrintAsAnyMessage(message)
    ):
      return
    fields = message.ListFields()
    if self.use_index_order:
      fields.sort(
          key=lambda x: x[0].number if x[0].is_extension else x[0].index
      )
    for field, value in fields:
      if _IsMapEntry(field):
        for key in sorted(value):
          # This is slow for maps with submessage entries because it copies the
          # entire tree.  Unfortunately this would take significant refactoring
          # of this file to work around.
          #
          # TODO: refactor and optimize if this becomes an issue.
          entry_submsg = value.GetEntryClass()(key=key, value=value[key])
          self.PrintField(field, entry_submsg)
      elif field.is_repeated:
        if (
            self.use_short_repeated_primitives
            and field.cpp_type != descriptor.FieldDescriptor.CPPTYPE_MESSAGE
            and field.cpp_type != descriptor.FieldDescriptor.CPPTYPE_STRING
        ):
          self._PrintShortRepeatedPrimitivesValue(field, value)
        else:
          for element in value:
            self.PrintField(field, element)
      else:
        self.PrintField(field, value)

    if self.print_unknown_fields:
```

**File:** src/google/protobuf/unittest_redaction.proto (L31-36)
```text

message TestNestedMessageEnum {
  repeated MetaAnnotatedEnum direct_enum = 1;
  TestMessageEnum nested_enum = 2;
  string redacted_string = 3 [debug_redact = true];
}
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
