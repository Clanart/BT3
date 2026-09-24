### Title
Any `type_url` Injection into TextFormat Output via Unescaped String Interpolation - (File: `src/google/protobuf/text_format.cc`)

### Summary
The SciTokens report shows a generic bug class: attacker-controlled string data (`issuer`, `key_id`) is spliced directly into a syntactic template (a SQL statement) via naive string formatting instead of being escaped/validated for the target grammar, letting the value "break out" of its intended slot and change the statement's structure. The Protobuf analog is `TextFormat::Printer::PrintAny` (and its Java/Python counterparts), which splices the attacker-controlled `Any.type_url` string directly between the literal `[` / `]` delimiters of TextFormat output with no validation that it only contains the character set that `any.proto` itself mandates for type URLs, and with none of the escaping normally applied to string field values.

### Finding Description
`Any.type_url` is an ordinary `string` field. Its content is fully attacker-controlled: any client sending a bounded, well-formed binary Protobuf message containing an `Any` submessage can set `type_url` to an arbitrary UTF‑8 byte sequence, subject only to standard string-field constraints, not to the documented URI-character restriction.

`any.proto` documents the required invariant explicitly: [1](#0-0) 

That invariant ("must consist only of alphanumeric characters, percent-encoded escapes, and characters in `/-.~_!$&()*+,;=`") is enforced only on the **parsing** side. `ConsumeAnyTypeUrlOrFullTypeName` validates percent-encoding and validates that the type name after the last `/` is composed of legal identifiers: [2](#0-1) 

No equivalent validation exists on the **printing** side. `TextFormat::Printer::PrintAny` takes the raw `type_url` field value and writes it verbatim between literal bracket tokens, with no escaping of characters that are syntactically meaningful in TextFormat (`]`, `#`, `"`, newline, `{`/`}`): [3](#0-2) 

The same unescaped-interpolation pattern exists in the Java runtime: [4](#0-3) 

and in the pure-Python runtime, where the value is inserted via `%`-formatting exactly like the vulnerable `str.format()` calls in the SciTokens `KeyCache`: [5](#0-4) 

Ordinary string *field values* printed by TextFormat go through an escaping/quoting helper (backslash-escaping quotes, control characters, etc.) before being emitted; the `type_url` bracket content bypasses that helper entirely because it is treated as a "type name," not a "string value." This is the exact analog of KeyCache treating `issuer`/`key_id` as safe template fragments instead of data requiring escaping.

For the injection to be observable, the attacker only needs the *type name* (the substring after the last `/`, used for descriptor-pool lookup) to resolve to some linked-in message type — e.g. a ubiquitous well-known type such as `google.protobuf.Empty` — while packing arbitrary bytes into the *prefix* portion of `type_url` (everything up to and including that last `/`). The prefix is never character-checked at print time, so it can contain `]`, `#`, newlines, or quote characters.

### Impact Explanation
Impact requires a consuming application that (a) parses untrusted binary Protobuf containing an `Any` field and (b) renders it with `TextFormat::PrintToString`/`DebugString` (or the Java/Python equivalents) for logging, audit trails, config snapshots, test golden files, or "reparse for diff" pipelines — all common, documented uses of TextFormat. In that scenario the attacker can inject additional, attacker-chosen field/text tokens into the generated TextFormat document. If that text is later fed back into `TextFormat::Parser::Merge` (a normal round-trip pattern for the format), the reparse can silently materialize extra fields the original binary message never actually contained, or desynchronize field boundaries in the surrounding message — an integrity violation directly analogous to the SQL injection's ability to alter query semantics. This does not grant memory corruption or RCE; the impact is confined to structured-text integrity/spoofing within the TextFormat serialization/deserialization surface.

### Likelihood Explanation
Likelihood is Medium: exploitation needs the specific combination of (1) an application that both prints and later reparses TextFormat containing an attacker-influenced `Any`, and (2) the attacker being able to make the trailing type-name segment resolve to a linked type while controlling the prefix bytes. Both preconditions are realistic — `Any` and TextFormat round-tripping are widely used together (e.g., in test tooling, config diffing, and logging pipelines) — but this is not a universal parse-time vulnerability triggered on every `ParseFromString` call the way the KeyCache SQLi was triggered on every cache lookup.

### Recommendation
On the TextFormat print path for `Any` (C++ `TextFormat::Printer::PrintAny`, Java `TextFormat.printAny`, Python `_TryPrintAsAnyMessage`, and any other language runtime with equivalent logic), validate that `type_url` conforms to the character restriction documented in `any.proto` before splicing it into the bracket syntax, or otherwise escape/quote it the same way ordinary string field values are escaped, rejecting/falling back to the non-expanded representation when the value contains TextFormat-significant characters (`]`, `#`, quote, newline, brace).

### Proof of Concept
Conceptual reproduction (matches the pattern validated in the codebase's own Any/TextFormat conformance tests, which show many characters — including `#` comment starts and whitespace — are accepted inside the `[...]` on the parse side, confirming these characters are not restricted anywhere on the print side either): [6](#0-5) 

1. Construct a valid, bounded binary Protobuf message `M` containing a field of type `google.protobuf.Any`.
2. Set `Any.type_url = "attacker] extra_injected_field: 1 #comment/google.protobuf.Empty"` and `Any.value = ""` (a valid empty encoding for `google.protobuf.Empty`).
3. Have the trusted application call `TextFormat::PrintToString(M)` (or Java/Python equivalent). Because `PrintAny` resolves the type name `google.protobuf.Empty` (found in the linked descriptor pool) and prints the entire `type_url` string raw between `[` and `]`:
   `field { [attacker] extra_injected_field: 1 #comment/google.protobuf.Empty] { } }`
4. Feeding this text back through `TextFormat::Parser::Merge` for the same message type shows the parser now accepts `extra_injected_field: 1` as a legitimate top-level field of the outer message and treats the remainder after `#` as a comment — content the original binary message never contained, demonstrating structural injection analogous to the KeyCache SQL injection PoC's manipulation of the generated `DELETE ... WHERE` statement.

I was not able to execute this PoC in a live checkout (no build/test execution available in this session); the finding is based on static code inspection of `text_format.cc`, `TextFormat.java`, and `text_format.py`, cross-checked against the documented invariant in `any.proto` and the existing conformance test corpus that exercises unusual characters inside `Any` brackets on the parser side only.

### Citations

**File:** src/google/protobuf/any.proto (L90-96)
```text
  // All type URL strings must be legal URI references with the additional
  // restriction (for the text format) that the content of the reference
  // must consist only of alphanumeric characters, percent-encoded escapes, and
  // characters in the following set (not including the outer backticks):
  // `/-.~_!$&()*+,;=`. Despite our allowing percent encodings, implementations
  // should not unescape them to prevent confusion with existing parsers. For
  // example, `type.googleapis.com%2FFoo` should be rejected.
```

**File:** src/google/protobuf/text_format.cc (L1432-1455)
```text
      // Validate URL percent encodings in prefix: Every '%' needs to be
      // followed by two hex characters.
      for (size_t i = 0; i < url_prefix.size(); ++i) {
        static constexpr absl::CharSet kHexDigits =
            absl::CharSet::AsciiHexDigits();
        if (url_prefix[i] == '%' && (i + 2 >= url_prefix.size() ||
                                     !kHexDigits.contains(url_prefix[i + 1]) ||
                                     !kHexDigits.contains(url_prefix[i + 2]))) {
          ReportError(absl::StrFormat("Invalid percent encode, got: \"%s\"",
                                      url_prefix.substr(i, 3)));
          return false;
        }
      }
    }

    // Validate type name: Must be non-empty and consist of valid identifiers
    // separated by '.'.
    for (absl::string_view identifier : absl::StrSplit(full_type_name, '.')) {
      if (!tokenizer_.IsIdentifier(identifier)) {
        ReportError(absl::StrFormat(
            "Invalid identifier in type name, got: \"%s\"", identifier));
        return false;
      }
    }
```

**File:** src/google/protobuf/text_format.cc (L2536-2566)
```text
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
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L436-461)
```java
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

**File:** python/google/protobuf/text_format.py (L429-441)
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
```

**File:** conformance/text_format_conformance_suite.cc (L898-916)
```text
  RunValidTextFormatTest(
      "AnyFieldWithWhitespaceInTypeUrl", REQUIRED,
      "optional_any: {\n"
      "  [ ty pe.go\nogleap\tis.com/\n"
      "    proto buf_te\nst_messages.proto3.Test\tAllTypesProto3 ]\n"
      "  {\n"
      "    optional_int32: 12345\n"
      "  }\n"
      "}");
  RunValidTextFormatTest(
      "AnyFieldWithCommentsInTypeUrl", REQUIRED,
      "optional_any: {\n"
      "  [type.google # comment \napis.com/"
      "protobuf_test_messages.proto3.Test # comment \nAllTypesProto3]"
      "  {\n"
      "    optional_int32: 12345\n"
      "  }\n"
      "}");
}
```
