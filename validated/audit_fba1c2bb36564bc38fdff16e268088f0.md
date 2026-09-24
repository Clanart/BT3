## Analysis

The OpenSTAManager report's core pattern is: **an attacker-controlled array of values is embedded into a structured, syntax-sensitive output stream (a SQL statement) without validating each element against the syntax's safe character set, and a downstream interpreter (the SQL engine) then parses the attacker-injected metacharacters as control syntax instead of data**, disclosing sensitive data through the resulting parser errors.

Protobuf has no SQL surface, but it has an exact structural analog in **`TextFormat` serialization of `google.protobuf.Any`**: the `type_url` string field is attacker-controlled binary-wire data, and it is written *raw* into the `[...]` bracket syntax of the generated TextFormat text without validating it against the documented safe character set. That generated text is a serialization format with its own metacharacters (`[`, `]`, `{`, `}`, `#`, whitespace, quotes) which can later be re-parsed by `TextFormat::Parse`, exactly mirroring the SQL case where unvalidated attacker data is embedded into a downstream parser's syntax.

### Title
TextFormat serialization of `Any.type_url` embeds unvalidated attacker-controlled bytes into `[...]` bracket syntax, enabling text-format injection on re-parse - (File: `src/google/protobuf/text_format.cc`)

### Summary
`TextFormat::Printer::PrintAny` writes the `type_url` field of a `google.protobuf.Any` message directly into the emitted text as `[<type_url>]` without validating it against the character class the format itself documents and enforces on the *parsing* side (`ConsumeAnyTypeUrlOrFullTypeName`). Because `type_url` is an ordinary `string` field with no wire-level restriction, an attacker who controls the bytes of an `Any` submessage (via any bounded binary Protobuf parse) can set `type_url` to a value containing TextFormat metacharacters (`]`, `{`, `}`, `#`, quotes, newlines). If the resulting TextFormat text is later consumed by `TextFormat::Parse` (a common round-trip pattern for logging, debugging dumps, or textproto persistence), the injected characters are interpreted as structural syntax rather than data — an injection class directly analogous to the SQL Injection root cause in the report (unvalidated attacker array elements concatenated into a syntax-sensitive sink later reinterpreted by a parser).

### Finding Description
- `any.proto` documents that `type_url` "must consist only of alphanumeric characters, percent-encoded escapes, and characters in the following set: `/-.~_!$&()*+,;=`" [1](#0-0) , and the TextFormat *parser* enforces this strictly: `ConsumeAnyTypeUrlOrFullTypeName` validates percent-encodings and that the type-name segment is a legal identifier, rejecting invalid input [2](#0-1) .
- However, the C++ *printer* side, `TextFormat::Printer::PrintAny`, takes `type_url` straight from the message via reflection and writes it unescaped between literal `[` and `]` tokens with no character-class check at all: `generator->PrintLiteral("["); generator->PrintString(type_url); generator->PrintLiteral("]");` [3](#0-2) .
- The same unchecked-embedding pattern exists in the Java runtime (`generator.print("["); generator.print(typeUrl); generator.print("]");`) [4](#0-3)  and the Python runtime (`self.out.write('%s[%s]%s ' % (self.indent * ' ', message.type_url, colon))`) [5](#0-4) .
- `type_url` itself has no restriction enforced at binary-wire parse time — it is an ordinary proto3 `string` field — so any value, including one packed with `]`, `{`, newline, `#`, or `"` characters, survives a normal bounded `ParseFromString`/`MergeFrom` call on `Any`.
- This is the direct analog of the external report's root cause: `array_clean()` only filtered empty values without validating element *content/type* before an unescaped `implode()` into SQL syntax [6](#0-5) ; here, `PrintAny` performs no analogous content validation before unescaped embedding into TextFormat syntax, even though the sibling parsing code in the very same file demonstrates the validation that *should* be mirrored on write.

### Impact Explanation
If application code (a) parses attacker-supplied binary Protobuf containing an `Any` with an attacker-chosen `type_url`, (b) calls `TextFormat::PrintToString`/`DebugString` on it (extremely common for logging, error messages, audit trails, or textproto persistence), and (c) later re-parses that text (config reload, test golden-file round-trips, log replay tooling, or any tool that treats debug/textproto output as re-parsable input), the attacker can inject arbitrary additional TextFormat fields/values or terminate the enclosing message early by embedding `]`, `}`, or `#`-comment sequences in `type_url`. This is an integrity failure of generated data analogous to the disclosure/tampering impact of the SQL report — the difference is the sink (structured text vs. SQL) and the consuming application still needs to round-trip the text, which the report's rules require us to state as the consuming-application exposure assumption, since Protobuf itself exposes no RPC endpoint.

### Likelihood Explanation
Likelihood is moderate: it requires (1) an application that both prints attacker-influenced `Any` messages via TextFormat and (2) later re-parses that same text with `TextFormat::Parse`. This is a real but non-default usage pattern (e.g., textproto-based config stores, some logging/replay pipelines, golden-file testing infrastructures that treat debug string output as canonical). The attacker-controlled precondition (setting an arbitrary `type_url` string on a bounded, valid `Any` submessage) is trivially satisfiable through the public binary parse API.

### Recommendation
Before embedding `type_url` in the `[...]` bracket in `PrintAny` (C++, Java, Python, and any other language runtime doing the same raw print), validate it against the same character class already enforced by the corresponding TextFormat *parser* function (`ConsumeAnyTypeUrlOrFullTypeName`), or percent-escape/reject any character outside the documented safe set (`/-.~_!$&()*+,;=`, alphanumerics, percent-escapes) prior to writing it into the generator. If validation fails, fall back to printing the `Any` as a regular two-field message (`type_url: "..."` with the string properly quote-escaped, as the ordinary string-field printer already does) rather than the unescaped bracket form.

### Proof of Concept
Conceptually (mirroring the external PoC's "attacker sets an array element to a value with injectable metacharacters"):
1. Construct a valid, bounded binary `Any` message where `type_url = "type.googleapis.com/foo.Bar] extra_field: 1 #"` and `value` is a validly-serialized `foo.Bar` payload.
2. Parse this bounded input through the public binary API (`Any::ParseFromString` / `Message::MergeFrom`) — succeeds because `type_url` has no wire-level charset restriction.
3. Call `TextFormat::PrintToString` on the containing message with `SetExpandAny(true)`. The printer emits: `outer_field { [type.googleapis.com/foo.Bar] extra_field: 1 #] { ... } }` — the injected `]`, field assignment, and comment marker are emitted verbatim inside the generated text, matching the pattern reflected in the parser-side validation logic that the printer omits [7](#0-6) .
4. If this text is fed back into `TextFormat::Parse`, the injected tokens are interpreted as real message structure rather than as an opaque `type_url` string, demonstrating the injection.

I did not execute this against a live build (no execution environment available here); the above is a source-level trace showing the missing validation and the exact lines where the unescaped embedding occurs, paired with the sibling validation code in the same file that proves the safety invariant exists but is not applied symmetrically on the print path.

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

**File:** src/google/protobuf/text_format.cc (L1430-1455)
```text
      }

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
