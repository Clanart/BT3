### Title
TextFormat Any/`type_url` expansion prints attacker-controlled string via unescaped `PrintString`, bypassing the standard control-character/escape sanitization applied to all other string fields - (File: `src/google/protobuf/text_format.cc`)

### Summary
The ack advisory's failed invariant is: "any printer that renders an attacker-controlled string to a display sink must apply the same escaping rules used everywhere else." Ack sanitized filenames in most code paths via `_safe_filename` but forgot to route `--show-types`, `-l/-L`, and `-c` through it, letting raw ANSI/control bytes reach the terminal. Protobuf's `TextFormat::Printer` has an analogous but narrower gap: when `SetExpandAny(true)` is used, the `type_url` of a `google.protobuf.Any` submessage — a plain, attacker-controlled `string` field with no character restrictions at the wire level — is written with the generator's raw `PrintString`/`PrintLiteral` primitives instead of going through the escaping field-value printer (`FastFieldValuePrinter::PrintString` / `HardenedPrintString`) that every other string/bytes field value passes through.

### Finding Description
Every ordinary string field in TextFormat output is escaped before being emitted. In C++, `FastFieldValuePrinter::PrintString` calls `ContainsCharactersToCEscape` and, if any byte `<32`, `>126`, `"`, `'`, or `\` is present, runs `absl::CEscape` on the value before printing: [1](#0-0) 
The UTF‑8-escaping variant additionally scans for invalid UTF‑8 while still escaping every unprintable/control byte via `DefinitelyNeedsEscape`/`HardenedPrintString`: [2](#0-1) 
The same discipline is duplicated identically in upb (`upb/text/internal/encode.h`), Java (`TextFormatEscaper.escapeText`), and Python (`text_encoding.CEscape`), all of which escape bytes `<0x20` and `0x7f` — i.e. exactly the ANSI/CSI control-byte range that the ack CVE is about.

However, `TextFormat::Printer::PrintAny` — invoked only when `expand_any_` is enabled — prints the `type_url` field with the generator's raw literal/string primitives, not the field-value escaping path: [3](#0-2) 
`generator->PrintString(type_url)` here calls `BaseTextGenerator::PrintString`, the low-level output primitive used to emit already-escaped text (e.g. the pre-escaped output of `CEscape`), not the higher-level `FastFieldValuePrinter::PrintString` that performs the escaping. Everywhere else in the same function the code correctly uses `GetFieldPrinter(...)->PrintString` (the escaping path) for actual field values; `type_url` is the one exception, printed directly as `"[" + type_url + "]"`.

The Java implementation shows the same pattern: `printAny` writes `generator.print(typeUrl)` directly, bypassing `TextFormatEscaper.escapeText`/`escapeDoubleQuotesAndBackslashes` that the normal `STRING` case in `printFieldValue` applies: [4](#0-3) [5](#0-4) 

`type_url` is fully attacker-controlled: it originates from a `string` field inside a nested `Any` message that the application decoded from an ordinary, bounded binary-protobuf or ProtoJSON payload via a supported public parse API (`ParseFromString`/`MergeFrom`). The wire format places no restriction on the bytes of a `string` field (aside from being valid UTF‑8, which still permits `ESC`, `CSI`, and other C0 control bytes — UTF‑8 validity and "safe for terminal" are orthogonal properties). Once such a message is later rendered with `TextFormat::Printer::SetExpandAny(true)` (a common option for human-readable debug dumps, e.g. `protobuf::field_extraction`/debugging tools, `PrintToString`, logging helpers), the `type_url` value is emitted between `[` and `]` completely unescaped.

### Impact Explanation
This transfers the exact bug class from the ack report: a string value that is *supposed to be* uniformly sanitized before being written to a human-facing sink (terminal, log viewer, CI console) escapes that sanitization on one specific code path. Consequences mirror the ack advisory — terminal cursor-movement/color escape sequences embedded in `type_url` can overwrite/spoof earlier terminal output when a consuming application feeds `TextFormat::PrintToString`/`DebugString`-with-expand-any output to a terminal or terminal-emulating log viewer (this is the assumed consuming-application exposure: Protobuf itself has no display surface, but its documented purpose for `TextFormat` is exactly "human-in-the-loop" viewing). This is a display/terminal-integrity issue, not memory corruption, matching the ack CVE's own class (CVSS vector with C:N/I:N/A:H — availability/spoofing of terminal state, no confidentiality/integrity impact on the process itself).

### Likelihood Explanation
Triggering requires only: (1) an `Any` field whose `type_url` contains ESC/CSI bytes, packaged in an otherwise valid, bounded protobuf message; (2) the receiving application decoding it via the ordinary public parse API; and (3) using `TextFormat::Printer` with `expand_any(true)` (or the Java/Python equivalents) to render it for human consumption, which is a documented, commonly used TextFormat feature for debugging Any-typed messages. No malicious schema, plugin, or privileged access is needed — the attacker only controls the bytes of an ordinary string field within a bounded message.

### Recommendation
Route `type_url` through the same escaping field-value printer used for ordinary `STRING` fields before emitting it in `PrintAny` (C++, `text_format.cc`) and `printAny` (Java, `TextFormat.java`), e.g. call `GetFieldPrinter(type_url_field)->PrintString(type_url, generator)` (or `TextFormatEscaper.escapeText`/`HardenedPrintString` in Java) instead of the raw `PrintString`/`print` primitive. Apply the analogous fix to any other language binding (`text_format.py`'s `_TryPrintAsAnyMessage`, which similarly writes `'%s[%s]%s ' % (..., message.type_url, ...)` without CEscape) that prints `type_url` outside the normal field-escaping path.

### Proof of Concept
Conceptual reproduction (not executed — sandbox limitations; this traces the exact code path with concrete evidence):
1. Build an `Any` message where `type_url = "type.googleapis.com/\x1b[31mFAKE\x1b[0m"` (embeds ANSI red-color escape sequences) and `value` = a validly serialized instance of a registered/trusted message type looked up by the suffix after the last `/` (the URL-parsing logic in `internal::ParseAnyTypeUrl`/`DefaultFinderFindAnyType` only needs the trailing type name to resolve; the prefix content, including embedded escape bytes, is not validated).
2. Serialize the containing message and parse it back with the ordinary public `ParseFromString` API (trusted schema, bounded payload).
3. Call `TextFormat::Printer printer; printer.SetExpandAny(true); printer.PrintToString(msg, &out);` (or Java `TextFormat.printer().usingTypeRegistry(...).printToString(msg)`).
4. Inspect `out`: the substring `"[" + type_url + "]"` at [6](#0-5)  contains the raw `\x1b[31m...\x1b[0m` bytes verbatim — unlike a normal `optional_string` field, which (per the `StringEscape`/`Utf8DebugString` tests at [7](#0-6) ) would have such control bytes rendered as `\033` octal escapes.

This confirms the check-bypass: the escaping invariant enforced everywhere else in `TextFormat::Printer` (`ContainsCharactersToCEscape`, `HardenedPrintString`, `TextFormatEscaper.escapeText`) is not applied to the `type_url` string when Any-expansion is used.

### Citations

**File:** src/google/protobuf/text_format.cc (L1877-1900)
```text
void TextFormat::Printer::HardenedPrintString(
    absl::string_view src, TextFormat::BaseTextGenerator* generator) {
  // Print as UTF-8, while guarding against any invalid UTF-8 in the string
  // field.
  //
  // If in the future we have a guaranteed invariant that invalid UTF-8 will
  // never be present, we could avoid the UTF-8 check here.

  generator->PrintLiteral("\"");
  while (!src.empty()) {
    size_t n = SkipPassthroughBytes(src);
    if (n != 0) {
      generator->PrintString(src.substr(0, n));
      src.remove_prefix(n);
      if (src.empty()) break;
    }

    // If repeated calls to CEscape() and PrintString() are expensive, we could
    // consider batching them, at the cost of some complexity.
    generator->PrintString(absl::CEscape(src.substr(0, 1)));
    src.remove_prefix(1);
  }
  generator->PrintLiteral("\"");
}
```

**File:** src/google/protobuf/text_format.cc (L2216-2238)
```text
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

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L418-461)
```java
    /**
     * Attempt to print the 'google.protobuf.Any' message in a human-friendly format. Returns false
     * if the message isn't a valid 'google.protobuf.Any' message (in which case the message should
     * be rendered just like a regular message to help debugging).
     */
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
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L633-640)
```java
        case STRING:
          generator.print("\"");
          generator.print(
              escapeNonAscii
                  ? TextFormatEscaper.escapeText((String) value)
                  : escapeDoubleQuotesAndBackslashes((String) value).replace("\n", "\\n"));
          generator.print("\"");
          break;
```

**File:** src/google/protobuf/text_format_unittest.cc (L507-530)
```text
TEST_F(TextFormatTest, StringEscape) {
  // Set the string value to test.
  proto_.set_optional_string(kEscapeTestString);

  // Get the DebugString from the proto.
  std::string debug_string = proto_.DebugString();
  std::string utf8_debug_string = proto_.Utf8DebugString();

  // Hardcode a correct value to test against.
  std::string correct_string =
      absl::StrCat(multi_line_debug_format_prefix_,
                   "optional_string: ", kEscapeTestStringEscaped, "\n");

  // Compare.
  EXPECT_EQ(correct_string, debug_string);
  // UTF-8 string is the same as non-UTF-8 because
  // the protocol buffer contains no UTF-8 text.
  EXPECT_EQ(correct_string, utf8_debug_string);

  std::string expected_short_debug_string =
      absl::StrCat(single_line_debug_format_prefix_,
                   "optional_string: ", kEscapeTestStringEscaped);
  EXPECT_EQ(expected_short_debug_string, proto_.ShortDebugString());
}
```
