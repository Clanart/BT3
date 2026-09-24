### Title
Missing `debug_redact` Enforcement in upb Text/Debug-String Encoder Leaks Sensitive Fields to Logs - (File: `upb/text/internal/encode.c`)

### Summary
Protobuf's C++ and Java runtimes implement a purpose-built defense against exactly the kube-router bug class (credential values printed in verbose/debug logs): the `debug_redact` field option, enforced by `TextFormat::Printer::TryRedactFieldValue` in C++ and `TextFormat.Printer.shouldRedact`/`DebugFormat` in Java. The upb-based text/debug-string encoder that backs Python's default `_upb` implementation, Ruby, and PHP native extensions contains no equivalent check, so a field annotated `debug_redact = true` is printed in cleartext whenever a message is converted to its debug string on those runtimes — silently defeating the redaction contract that application code (and other language runtimes) rely on.

### Finding Description
The kube-router vulnerability's failed invariant is: "a protective, password-masking `Stringer` exists (`PeerConfig.String()`), but one log call site (`klog.V(2).Infof("...%+v", node.Annotations)`) bypasses it and dumps the raw sensitive map instead."

Protobuf has the direct analog of that protective `Stringer`: the `debug_redact` `FieldOptions` bit, checked via:
- C++: `TextFormat::Printer::TryRedactFieldValue` [1](#0-0) , driven by `TextFormat::GetRedactionState`/`IsOptionSensitive` [2](#0-1) , and enabled by default for `Message::DebugString()` via `StringifyMessage`, which explicitly calls `printer.SetRedactDebugString(true)` [3](#0-2) .
- Java: `TextFormat.Printer.shouldRedact` and the `DebugFormat` class [4](#0-3) [5](#0-4) , and `AbstractMessage.toString()` routes through the redaction-aware printer by default [6](#0-5) .

However, the upb kernel's debug-string encoder (`upb/text/internal/encode.c`), which several language bindings use as their native backend, never consults `debug_redact` at all. `_upb_FieldDebugString` prints scalar/message field values unconditionally based only on `upb_CType`, with no call to any redaction/sensitivity accessor on the `upb_MiniTableField` [7](#0-6) , and `_upb_MessageDebugString`, which walks base fields, maps, arrays, and extensions to build the full debug string, likewise performs no redaction check anywhere in its traversal [8](#0-7) . The `debug_redact` option itself is defined and available in the upb-generated descriptor code (`descriptor.upb.h`), so the metadata needed to redact is present at the MiniTable/option level — it is simply never read by the encoder. This means the check "is this FieldDescriptor's option `debug_redact == true`" that exists in the C++ and Java text formatters is entirely absent from the upb text encoder's code path.

The attacker-controlled value here is any field value carried in a schema-defined sensitive field (e.g., an API token or password field the application's `.proto` schema marks `debug_redact = true`) that the attacker supplies via a bounded, valid binary Protobuf or ProtoJSON payload through the application's normal parse API. Once parsed into a message object, if the consuming application later stringifies that message for debug/error logging (a common and documented practice, exactly as kube-router's own troubleshooting docs recommended `-v=2` before filing issues), a upb-backed runtime will emit the "redacted" field's raw value into logs, while the same code running on the C++ or Java runtime would correctly mask it.

### Impact Explanation
This breaks the confidentiality guarantee that `debug_redact` is documented and tested to provide (see the C++/Java `RedactedFields` test protos and `DebugFormatTest`/`text_format_unittest.cc` suites, which assert that marked fields print as `[REDACTED]`). Applications built on upb-backed language runtimes that rely on `debug_redact` to keep secrets out of debug logs, crash reports, or error telemetry will leak those values whenever a message containing attacker-influenced data is logged via the default debug-string mechanism — the same class of impact as the original CWE-532 report (credential/secret disclosure through logs), scoped here to the consuming application's log/telemetry surface rather than BGP peers specifically.

### Likelihood Explanation
Likelihood is Medium: it requires (1) an application schema author to have marked a sensitive field `debug_redact = true` expecting protection, (2) the application to be running on (or interoperate with, e.g. via shared `.proto` files and mixed-language services) a upb-backed binding, and (3) application code to log a parsed message via a standard string/debug conversion path. Given `debug_redact` is a first-class, documented protobuf feature intended precisely to prevent this class of leak, and debug-logging of received messages is an extremely common operational practice, this is a realistic, low-effort-to-trigger gap once the schema/runtime combination is in place. I was not able to fully trace, within the available iterations, the exact call chain from each language's public `__str__`/`to_s`/`__toString` entry point down into `upb/text/internal/encode.c` for every upb-based binding in this checkout (Python `_upb`, Ruby, PHP), so it should be verified per-binding that the default stringification indeed dispatches to this encoder before treating this as confirmed exploitable in each of those bindings specifically.

### Recommendation
Add `debug_redact` enforcement to the upb text/debug-string encoder, mirroring the C++/Java implementations:
- In `_upb_FieldDebugString` (and its map/array/extension counterparts) in `upb/text/internal/encode.c`, check the field's `debug_redact` option (available via the MiniTable/option metadata) before printing scalar, string, or nested-message values, and substitute a fixed `[REDACTED]`-style marker when set — recursing into submessages to redact nested `debug_redact` fields as C++'s `IsOptionSensitive` does.
- Audit each upb-backed language binding's default `__str__`/`to_s`/`__toString`/`inspect` implementation to confirm it goes through the (now-redacting) debug encoder rather than a raw dump, and add parity tests analogous to `text_format_unittest.cc`'s `ShortFormat`/`RedactedFields` tests and Java's `DebugFormatTest` for each affected binding.

### Proof of Concept
Given a schema (trusted, application-defined) such as:
```protobuf
message LoginRequest {
  string username = 1;
  string password = 2 [debug_redact = true];
}
```
1. An ordinary client sends a bounded, valid binary-encoded `LoginRequest` (e.g., `username: "alice"`, `password: "s3cr3t"`) through the application's public parse API (`LoginRequest.ParseFromString(data)` / `LoginRequest.FromString(data)` depending on binding) on a upb-backed runtime (e.g., Python's default `_upb` implementation, or Ruby/PHP native extensions built on upb).
2. Application error-handling or debug-logging code calls the message's default string conversion (`str(request)` / `request.to_s` / `(string) $request` or logs the object directly, which invokes `__str__`/`__repr__`/`inspect`).
3. That call reaches `upb_MiniTable`-driven debug-string generation via `_upb_MessageDebugString` → `_upb_FieldDebugString` in `upb/text/internal/encode.c`, which prints `password: "s3cr3t"` verbatim, because no `debug_redact` check exists in that code path [9](#0-8) .
4. Contrast: the same schema/message, when debug-printed via the C++ (`Message::DebugString()`) or Java (`AbstractMessage.toString()`/`DebugFormat`) runtimes, produces `password: [REDACTED]` because those paths invoke `TryRedactFieldValue`/`shouldRedact` [10](#0-9) .

I was not able to execute this PoC against a live build within this session; the finding is based on static code inspection showing the redaction check present in C++/Java and structurally absent from the upb encoder's field/message traversal functions cited above. A background engineer should compile a small upb-backed binding (Python `_upb` or Ruby) with a `debug_redact` field and confirm the plaintext leak experimentally before filing/fixing.

### Citations

**File:** src/google/protobuf/text_format.cc (L118-141)
```text
std::string StringifyMessage(const Message& message, Option option,
                             FieldReporterLevel reporter_level) {
  // Indicate all scoped reflection calls are from DebugString function.
  ScopedReflectionMode scope(ReflectionMode::kDebugString);

  TextFormat::Printer printer;
  internal::FieldReporterLevel reporter = reporter_level;
  switch (option) {
    case Option::kShort:
      printer.SetSingleLineMode(true);
      break;
    case Option::kUTF8:
      printer.SetUseUtf8StringEscaping(true);
      break;
    case Option::kNone:
      break;
  }
  printer.SetExpandAny(true);
  printer.SetRedactDebugString(true);
  printer.SetRandomizeDebugString(true);
  printer.SetReportSensitiveFields(reporter);
  std::string result;
  // TODO: Remove this suppression.
  (void)printer.PrintToString(message, &result);
```

**File:** src/google/protobuf/text_format.cc (L3222-3278)
```text
TextFormat::RedactionState TextFormat::IsOptionSensitive(
    const Message& opts, const Reflection* reflection,
    const FieldDescriptor* option) {
  if (option->type() == FieldDescriptor::TYPE_ENUM) {
    auto count =
        option->is_repeated() ? reflection->FieldSize(opts, option) : 1;
    for (auto i = 0; i < count; i++) {
      int enum_val = option->is_repeated()
                         ? reflection->GetRepeatedEnumValue(opts, option, i)
                         : reflection->GetEnumValue(opts, option);
      const EnumValueDescriptor* option_value =
          option->enum_type()->FindValueByNumber(enum_val);
      if (option_value == nullptr) {
        // Ignore values we don't know about.
        continue;
      }
      if (option_value->options().debug_redact()) {
        return TextFormat::RedactionState{true, false};
      }
    }
  } else if (option->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
    auto count =
        option->is_repeated() ? reflection->FieldSize(opts, option) : 1;
    for (auto i = 0; i < count; i++) {
      const Message& sub_message =
          option->is_repeated()
              ? reflection->GetRepeatedMessage(opts, option, i)
              : reflection->GetMessage(opts, option);
      const Reflection* sub_reflection = sub_message.GetReflection();
      std::vector<const FieldDescriptor*> message_fields;
      sub_reflection->ListFields(sub_message, &message_fields);
      for (const FieldDescriptor* message_field : message_fields) {
        auto result = TextFormat::IsOptionSensitive(sub_message, sub_reflection,
                                                    message_field);
        if (result.redact) {
          return result;
        }
      }
    }
  }
  return TextFormat::RedactionState{false, false};
}

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

**File:** java/core/src/main/java/com/google/protobuf/DebugFormat.java (L1-21)
```java
package com.google.protobuf;

import com.google.protobuf.Descriptors.FieldDescriptor;

/**
 * Provides an explicit API for unstable, redacting debug output suitable for debug logging. This
 * implementation is based on TextFormat, but should not be parsed.
 */
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
```

**File:** java/core/src/main/java/com/google/protobuf/AbstractMessage.java (L92-96)
```java
  @Override
  public final String toString() {
    return TextFormat.Printer.getOutputModePrinter()
        .printToString(this, TextFormat.Printer.FieldReporterLevel.ABSTRACT_TO_STRING);
  }
```

**File:** upb/text/internal/encode.c (L238-283)
```c
static void _upb_FieldDebugString(txtenc* e, upb_MessageValue val,
                                  const upb_MiniTableField* f,
                                  const upb_MiniTable* mt, const char* label,
                                  const upb_MiniTableExtension* ext) {
  UPB_PRIVATE(_upb_TextEncode_Indent)(e);
  const upb_CType ctype = upb_MiniTableField_CType(f);
  const bool is_ext = upb_MiniTableField_IsExtension(f);
  char number[10];  // A 32-bit integer can hold up to 10 digits.
  snprintf(number, sizeof(number), "%" PRIu32, upb_MiniTableField_Number(f));
  // label is to pass down whether we're dealing with a "key" of a map or
  // a "value" of a map.
  if (!label) label = number;

  if (is_ext) {
    UPB_PRIVATE(_upb_TextEncode_Printf)(e, "[%s]", label);
  } else {
    UPB_PRIVATE(_upb_TextEncode_Printf)(e, "%s", label);
  }

  if (ctype == kUpb_CType_Message) {
    UPB_PRIVATE(_upb_TextEncode_Printf)(e, " {");
    UPB_PRIVATE(_upb_TextEncode_EndField)(e);
    e->indent_depth++;
    const upb_MiniTable* subm = ext ? upb_MiniTableExtension_GetSubMessage(ext)
                                    : upb_MiniTable_SubMessage(f);
    UPB_PRIVATE(_upb_MessageDebugString)(e, val.msg_val, subm);
    e->indent_depth--;
    UPB_PRIVATE(_upb_TextEncode_Indent)(e);
    UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "}");
    UPB_PRIVATE(_upb_TextEncode_EndField)(e);
    return;
  }

  UPB_PRIVATE(_upb_TextEncode_Printf)(e, ": ");

  if (ctype ==
      kUpb_CType_Enum) {  // Enum has to be processed separately because of
                          // divergent behavior between encoders
    UPB_PRIVATE(_upb_TextEncode_Printf)(e, "%" PRId32, val.int32_val);
  } else {
    UPB_PRIVATE(_upb_TextEncode_Scalar)(e, val, ctype);
  }

  UPB_PRIVATE(_upb_TextEncode_EndField)(e);
}

```

**File:** upb/text/internal/encode.c (L363-405)
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
