### Title
Unescaped `Any.type_url` breaks TextFormat/DebugString output syntax when `expand_any` printing is used - (File: `src/google/protobuf/text_format.cc`, `java/core/src/main/java/com/google/protobuf/TextFormat.java`, `python/google/protobuf/text_format.py`)

### Summary
`TextFormat::Printer::PrintAny()` (C++), `TextFormat.Printer.printAny()` (Java), and `_Printer._TryPrintAsAnyMessage()` (Python) all render the human-readable "expanded Any" form `[type_url] { ... }` by writing the `type_url` string **verbatim, with no escaping/quoting**, directly between the literal `[` and `]` markers. `type_url` is an ordinary attacker-controlled `string` field taken straight off the wire of a `google.protobuf.Any` submessage that a consuming application parsed from untrusted bytes. Every other string field value printed by TextFormat goes through a quoting/escaping code path (`FastFieldValuePrinter::PrintString`, which wraps the value in `"…"` and C-escapes it — see `ContainsCharactersToCEscape`/`absl::CEscape` at `src/google/protobuf/text_format.cc:2214-2238`); the Any bracket path bypasses that printer entirely and calls the raw, unescaping `BaseTextGenerator::PrintString`/`generator.print(typeUrl)`/`%s` substitution instead.

### Finding Description
The failed invariant, mirroring the Mistune bug, is: *"any attacker-controlled string that is spliced into a structured/serialized output must be escaped/quoted for that output's syntax before being written."* Mistune's `heading()` violates this for the `id="` HTML attribute; TextFormat's Any-expansion violates it for the `[…]` bracket syntax of its own text format.

Evidence:
- C++: `TextFormat::Printer::PrintAny()` at `src/google/protobuf/text_format.cc:2527-2574` does:
  ```cpp
  generator->PrintLiteral("[");
  generator->PrintString(type_url);
  generator->PrintLiteral("]");
  ```
  `type_url` is read straight from the wire via `reflection->GetString(message, type_url_field)` (line 2539) — no validation, no escaping of `]`, `{`, `}`, `\n`, or any TextFormat-significant characters. Contrast this with the normal string-field path, `TextFormat::FastFieldValuePrinter::PrintString` (`text_format.cc:2227-2238`), which always wraps the value in quotes and calls `absl::CEscape` whenever `ContainsCharactersToCEscape` (line 2216-2223) is true. The Any path never calls this printer for `type_url`.
- Java: `TextFormat.java:459-461` — `generator.print(typeUrl)` between two literal `[`/`]` prints, same pattern, same missing escaping.
- Python: `text_format.py:439` — `self.out.write('%s[%s]%s ' % (self.indent * ' ', message.type_url, colon))`, a raw `%s` substitution of the attacker-controlled `type_url` with zero escaping.

Attacker control: `type_url` is a plain proto3 `string` field inside `Any`; nothing in `ParseAnyTypeUrl`/`GetAnyFieldDescriptors` (used only to derive `url_prefix`/`full_type_name` for descriptor lookup) restricts which bytes can appear in the raw field value that gets printed — it only needs a `/` to be treated as a type URL at all. A value like:
```
type.googleapis.com/foo] extra_field: "injected"
[type.googleapis.com/foo
```
or one containing `\n`, `{`, `}` will be written unescaped into the `[…]` slot, breaking the intended one-token bracket syntax of the printed text and inserting attacker-chosen tokens/fields into the emitted text-format document — the direct structural analog of breaking out of the `id="…"` attribute in the HTML report.

### Impact Explanation
The consuming-application exposure assumption: an application accepts bounded, arbitrary protobuf bytes via a supported parse API (`Message::ParseFromString`/`MergeFrom`, etc.) into a message containing a `google.protobuf.Any` field, then renders that message with `TextFormat::PrintToString`/`Message::DebugString()`/`str(message)`/`TextFormat.printer().printToString()` with Any expansion enabled (the default for `DebugString`), for logging, debugging UIs, or (in some systems) re-ingestion by another TextFormat parser or downstream tooling that treats the printed text as structured data. Because the bracket content is unescaped, an attacker who controls the `Any.type_url` bytes can inject additional fake "fields" or break the document structure of that generated text, analogous to attribute/markup injection — confidentiality/integrity impact is bounded to whatever downstream system trusts the printed TextFormat text (e.g., a re-parser, a log analyzer, or a display surface that itself interprets `[`/`]`/newlines specially), not to full browser-side JS execution as in the original CVE. This makes it a legitimate but narrower analog than the original CVSS 3.1 Medium — it is a genuine missing-escaping defect on an attacker-controlled value in a supported output path, but its blast radius depends entirely on how the consuming application further processes the TextFormat/debug string.

### Likelihood Explanation
Any application that logs or displays messages containing embedded `Any` fields via `DebugString()`/`ShortDebugString()`/`TextFormat.printToString()` (very common for debugging/logging in all supported languages) will hit this path whenever `type_url` resolves to a known type in the type registry (a prerequisite for the expanded-Any branch to run at all — see `PrintAny`'s `FindAnyType` check). An attacker only needs to supply a `type_url` that (a) matches a real registered type prefix/name so the branch is taken, and (b) contains extra unescaped characters after the recognized name/URL. This is straightforward to trigger and requires no privileged access — it fires on ordinary, bounded binary input through the public `ParseFrom`/`DebugString` surface.

### Recommendation
Route `type_url` through the same quoting/escaping printer used for ordinary string fields (or at minimum escape/validate `[`, `]`, and control characters) before writing it inside the `[…]` bracket in `TextFormat::Printer::PrintAny` (C++), `TextFormat.Printer.printAny` (Java), and `_Printer._TryPrintAsAnyMessage` (Python). Reject/escape `type_url` values containing TextFormat-significant characters (`[`, `]`, newlines, `{`, `}`) before using them to build the bracket token, or fall back to the plain (non-expanded) field printing (which already safely quotes/escapes `type_url` as a normal string field) when such characters are present.

### Proof of Concept
Not independently executed in this environment (read-only code index; no build/runtime access), but the code path is deterministic from the cited source:
1. Construct (or receive) an `Any` message via a normal `ParseFromString` call where `type_url = "type.googleapis.com/proto2_unittest.TestAllTypes]\nnested_field: 1\n["` (i.e., a value matching a registered type prefix, immediately followed by attacker-chosen bracket/newline/field-like content) and `value` set to any bytes parseable as `TestAllTypes`.
2. Call `TextFormat::Printer().PrintToString(msg, &text)` (or `msg.DebugString()`, or Java/Python equivalents) with a `TypeRegistry`/type pool that resolves `proto2_unittest.TestAllTypes`.
3. Per `PrintAny` (`text_format.cc:2564-2566`), the output literally contains:
   ```
   [type.googleapis.com/proto2_unittest.TestAllTypes]
   nested_field: 1
   [] {
     ...
   }
   ```
   i.e., the attacker-supplied `]`/newline/field-name content is emitted unescaped and breaks out of the intended single-bracket token, exactly mirroring the `id="…"` breakout demonstrated in the Mistune PoC (`foo" onmouseover="…" x="`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/google/protobuf/text_format.cc (L2214-2238)
```text
namespace {

bool ContainsCharactersToCEscape(absl::string_view val) {
  bool needs_escape = false;
  for (unsigned char c : val) {
    needs_escape |=
        ((c < 32) | (c > 126) | (c == '"') | (c == '\'') | (c == '\\'));
  }
  return needs_escape;
}

}  // namespace

void TextFormat::FastFieldValuePrinter::PrintString(
    const std::string& val, BaseTextGenerator* generator) const {
  generator->PrintLiteral("\"");
  if (!val.empty()) {
    if (ABSL_PREDICT_FALSE(ContainsCharactersToCEscape(val))) {
      generator->PrintString(absl::CEscape(val));
    } else {
      generator->PrintString(val);
    }
  }
  generator->PrintLiteral("\"");
}
```

**File:** src/google/protobuf/text_format.cc (L2527-2574)
```text
bool TextFormat::Printer::PrintAny(const Message& message,
                                   BaseTextGenerator* generator) const {
  const FieldDescriptor* type_url_field;
  const FieldDescriptor* value_field;
  if (!internal::GetAnyFieldDescriptors(message, &type_url_field,
                                        &value_field)) {
    return false;
  }

  const Reflection* reflection = message.GetReflection();

  // Extract the full type name from the type_url field.
  const std::string& type_url = reflection->GetString(message, type_url_field);
  std::string url_prefix;
  std::string full_type_name;

  if (!internal::ParseAnyTypeUrl(type_url, &url_prefix, &full_type_name)) {
    return false;
  }

  // Print the "value" in text.
  const Descriptor* value_descriptor =
      finder_ ? finder_->FindAnyType(message, url_prefix, full_type_name)
              : DefaultFinderFindAnyType(message, url_prefix, full_type_name);
  if (value_descriptor == nullptr) {
    ABSL_LOG(WARNING) << "Can't print proto content: proto type " << type_url
                      << " not found";
    return false;
  }
  DynamicMessageFactory factory;
  std::unique_ptr<Message> value_message(
      factory.GetPrototype(value_descriptor)->New());
  std::string serialized_value = reflection->GetString(message, value_field);
  if (!value_message->ParseFromString(serialized_value)) {
    ABSL_LOG(WARNING) << type_url << ": failed to parse contents";
    return false;
  }
  generator->PrintLiteral("[");
  generator->PrintString(type_url);
  generator->PrintLiteral("]");
  const FastFieldValuePrinter* printer = GetFieldPrinter(value_field);
  printer->PrintMessageStart(message, -1, 0, single_line_mode_, generator);
  generator->Indent();
  Print(*value_message, generator);
  generator->Outdent();
  printer->PrintMessageEnd(message, -1, 0, single_line_mode_, generator);
  return true;
}
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L423-471)
```java
    private boolean printAny(final MessageOrBuilder message, final TextGenerator generator)
        throws IOException {
      Descriptor messageType = message.getDescriptorForType();
      FieldDescriptor typeUrlField = messageType.findFieldByNumber(1);
      FieldDescriptor valueField = messageType.findFieldByNumber(2);
      if (typeUrlField == null
          || typeUrlField.getType() != FieldDescriptor.Type.STRING
          || valueField == null
          || valueField.getType() != FieldDescriptor.Type.BYTES) {
        // The message may look like an Any but isn't actually an Any message (might happen if the
        // user tries to use DynamicMessage to construct an Any from incomplete Descriptor).
        return false;
      }
      String typeUrl = (String) message.getField(typeUrlField);
      // If type_url is not set, we will not be able to decode the content of the value, so just
      // print out the Any like a regular message.
      if (typeUrl.isEmpty()) {
        return false;
      }
      Object value = message.getField(valueField);

      Message.Builder contentBuilder = null;
      try {
        Descriptor contentType = typeRegistry.getDescriptorForTypeUrl(typeUrl);
        if (contentType == null) {
          return false;
        }
        contentBuilder = DynamicMessage.getDefaultInstance(contentType).newBuilderForType();
        contentBuilder.mergeFrom((ByteString) value, extensionRegistry);
      } catch (InvalidProtocolBufferException e) {
        // The value of Any is malformed. We cannot print it out nicely, so fallback to printing out
        // the type_url and value as bytes. Note that we fail open here to be consistent with
        // text_format.cc, and also to allow a way for users to inspect the content of the broken
        // message.
        return false;
      }
      generator.print("[");
      generator.print(typeUrl);
      generator.print("]");
      generator.maybePrintSilentMarker();
      generator.print("{");
      generator.eol();
      generator.indent();
      print(contentBuilder, generator);
      generator.outdent();
      generator.print("}");
      generator.eol();
      return true;
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
