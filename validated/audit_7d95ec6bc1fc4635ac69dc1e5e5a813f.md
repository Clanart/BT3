### Title
upb `_upb_MessageDebugString`/`_upb_FieldDebugString` omit `debug_redact` enforcement, leaking fields marked sensitive in debug output - (File: `upb/text/internal/encode.c`)

### Summary
The GitLab report (CVE-2022-4343) describes a case where a security control intended to keep a "sensitive credential" out of an unauthorized viewer's reach was not actually enforced on a particular access path, so a party who should only see a scrubbed/limited representation could still obtain the raw secret. The transferable invariant for Protobuf is not "user permissions," but the library's own declared sensitive-field contract: a field annotated `[debug_redact = true]` in `FieldOptions`/`EnumValueOptions` is a documented promise ("fields... should not be printed out when using debug formats, e.g. when the field contains sensitive credentials", `src/google/protobuf/descriptor.proto:776-778`) that any debug/log-style stringification of the message will not emit that field's raw value. The C++ and Java `TextFormat` printers implement this promise centrally via `TryRedactFieldValue`/`shouldRedact`, gated on `redact_debug_string_`/`enablingSafeDebugFormat` ( [1](#0-0) , [2](#0-1) ). The upb text-debug encoder, used as the native backend for Python/Ruby/PHP/JS/Ruby bindings' debug string paths, has no equivalent check anywhere in its field/array/map/message debug-string emission path.

### Finding Description
`upb/text/internal/encode.c` implements `UPB_PRIVATE(_upb_MessageDebugString)`, which iterates base fields, extension fields, and unknown fields and dispatches to `_upb_FieldDebugString`, `_upb_ArrayDebugString`, and `_upb_MapEntryDebugString` to render the debug string [3](#0-2) . None of these functions consult `FieldOptions.debug_redact` (or `EnumValueOptions.debug_redact`) before writing the raw field value; the redaction check that C++/Java apply via `TryRedactFieldValue`/`GetRedactionState`/`shouldRedact` ( [4](#0-3) , [5](#0-4) , [6](#0-5) ) has no counterpart in `upb/text/internal/encode.c`. A repo-wide search for `redact` under `upb/**` only turns up the generated descriptor accessors for the `debug_redact` option field itself (`upb/reflection/stage0/google/protobuf/descriptor.upb.h`), not any consumption of that flag inside the text encoder. This means: given a trusted schema that marks a field `debug_redact = true` (exactly the "contains sensitive credentials" case the option's own documentation calls out, `src/google/protobuf/descriptor.proto:776-778`, `931-932`), an ordinary client's bounded, valid protobuf message containing that field, when stringified through upb's debug-string code path, will have the sensitive value printed verbatim — the redaction invariant that upstream Protobuf itself defines and enforces in the C++/Java text formatter is silently bypassed in the upb backend.

### Impact Explanation
Applications built on upb-backed language bindings (e.g., Python/PHP upb kernel, Ruby's `to_s`/inspect via native debug string, or any consumer calling into `_upb_MessageDebugString`) that rely on `debug_redact` to keep credentials/secrets out of logs, crash reports, or error messages will have those secrets exposed to anyone who can read that debug output — mirroring the GitLab CVE's "credentials leaked to a party who should not see them" pattern, but rooted in Protobuf's own text-encoding redaction contract rather than GitLab's authorization model. This is an information-disclosure defect (not memory corruption), consistent with a Medium exposure rating, since exploitation only requires a schema field annotated `debug_redact = true` and a normal, valid message — no privileged access, malicious peer, or resource exhaustion is needed.

### Likelihood Explanation
High, within the qualifying subset of upb-backed consumers that call the upb text/debug-string encoder and that rely on `debug_redact` for compliance/safety reasons (this is literally the documented purpose of the field, `src/google/protobuf/descriptor.proto:776-778`). No attacker action beyond constructing an ordinary message with a sensitive field populated is required; the omission is unconditional in the code path shown (`_upb_MessageDebugString`, `_upb_FieldDebugString`, `_upb_ArrayDebugString`, `_upb_MapEntryDebugString`), not input-dependent.

### Recommendation
Add a `debug_redact` check (mirroring `TextFormat::GetRedactionState`/`IsOptionSensitive` in `src/google/protobuf/text_format.cc:3219-3278`) to `upb/text/internal/encode.c`'s field-emission functions (`_upb_FieldDebugString`, `_upb_ArrayDebugString`, `_upb_MapEntryDebugString`, and the extension path in `_upb_MessageDebugString`), consulting `upb_MiniTableField`/`FieldOptions` reflection data to substitute a redaction marker instead of the raw value whenever `debug_redact` is set (directly or via a sensitive enum option), consistent with the C++/Java behavior.

### Proof of Concept
A minimal local reproduction would require:
1. A trusted `.proto` schema with a field `secret_credential` annotated `[debug_redact = true]` (as in the test fixture `RedactedFields.optional_redacted_string` used by C++/Java tests, `src/google/protobuf/unittest.proto:2464-2467`).
2. Build/parse a bounded, valid message via upb setting `secret_credential = "top-secret-value"`.
3. Call the upb debug-string path (`UPB_PRIVATE(_upb_MessageDebugString)` / the language-binding wrapper exposing it, e.g. Python/PHP/Ruby's message `__str__`/`inspect`).
4. Assert the output contains `top-secret-value` unredacted, whereas the equivalent C++/Java `TextFormat::Printer` with `redact_debug_string_`/`enablingSafeDebugFormat` enabled on the identical schema/message would emit the `<REDACTED>` marker (see `kFieldValueReplacement`/`REDACTED_MARKER` usage at `src/google/protobuf/text_format.cc:3287-3300` and `java/core/src/main/java/com/google/protobuf/TextFormat.java:591-597`).

I was not able to execute this reproduction in this environment (no test runner/tool access here) — the finding is based on static code comparison between the upb encoder and the C++/Java redaction logic; a Devin session with repo/build access would be needed to actually run the PoC and confirm the observed output difference.

### Citations

**File:** src/google/protobuf/text_format.cc (L2864-2868)
```text
    if (field->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
      if (TryRedactFieldValue(message, field, generator,
                              /*insert_value_separator=*/true)) {
        break;
      }
```

**File:** src/google/protobuf/text_format.cc (L2943-2947)
```text
  const FastFieldValuePrinter* printer = GetFieldPrinter(field);
  if (TryRedactFieldValue(message, field, generator,
                          /*insert_value_separator=*/false)) {
    return;
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

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L588-597)
```java
    private void printFieldValue(
        final FieldDescriptor field, final Object value, final TextGenerator generator)
        throws IOException {
      if (shouldRedact(field, generator)) {
        generator.print(REDACTED_MARKER);
        if (field.getJavaType() == FieldDescriptor.JavaType.MESSAGE) {
          generator.eol();
        }
        return;
      }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L673-676)
```java
    private boolean shouldRedact(final FieldDescriptor field, TextGenerator generator) {
      FieldDescriptor.RedactionState state = field.getRedactionState();
      return enablingSafeDebugFormat && state.redact;
    }
```

**File:** upb/text/internal/encode.c (L363-404)
```c
void UPB_PRIVATE(_upb_MessageDebugString)(txtenc* e, const upb_Message* msg,
                                          const upb_MiniTable* mt) {
  size_t iter = kUpb_BaseField_Begin;
  const upb_MiniTableField* f;
  upb_MessageValue val;

  // Base fields will be printed out first, followed by extension fields, and
  // finally unknown fields.

  while (UPB_PRIVATE(_upb_Message_NextBaseField)(msg, mt, &f, &val, &iter)) {
    if (upb_MiniTableField_IsMap(f)) {
      _upb_MapDebugString(e, val.map_val, f, mt);
    } else if (upb_MiniTableField_IsArray(f)) {
      // ext set to NULL as we're not dealing with extensions yet
      _upb_ArrayDebugString(e, val.array_val, f, mt, NULL);
    } else {
      // ext set to NULL as we're not dealing with extensions yet
      // label set to NULL as we're not currently working with a MapEntry
      _upb_FieldDebugString(e, val, f, mt, NULL, NULL);
    }
  }

  const upb_MiniTableExtension* ext;
  upb_MessageValue val_ext;
  iter = kUpb_Message_ExtensionBegin;
  while (upb_Message_NextExtension(msg, &ext, &val_ext, &iter)) {
    const upb_MiniTableField* f = &ext->UPB_PRIVATE(field);
    // It is not sufficient to only pass |f| as we lose valuable information
    // about sub-messages. It is required that we pass |ext|.
    if (upb_MiniTableField_IsMap(f)) {
      UPB_UNREACHABLE();  // Maps cannot be extensions.
      break;
    } else if (upb_MiniTableField_IsArray(f)) {
      _upb_ArrayDebugString(e, val_ext.array_val, f, mt, ext);
    } else {
      // label set to NULL as we're not currently working with a MapEntry
      _upb_FieldDebugString(e, val_ext, f, mt, NULL, ext);
    }
  }

  UPB_PRIVATE(_upb_TextEncode_ParseUnknown)(e, msg);
}
```
