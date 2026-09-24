Based on my investigation, I found a plausible but only partially-provable analog. I was unable to fully verify the downstream parser behavior for a complete proof (see caveats below), but the code-level evidence for the missing escaping is confirmed.

### Title
TextFormat's `Any` printer emits `type_url` unescaped inside `[...]` delimiters, allowing textproto structure injection - ([File: src/google/protobuf/text_format.cc])

### Summary
`TextFormat::Printer::PrintAny()` writes the `Any.type_url` string field directly between literal `[` and `]` delimiters without applying the same escaping logic used for ordinary string field values. `Any.type_url` is a normal, attacker-controlled `string` field (populated via public binary/ProtoJSON parsing of an `Any` message), and its content is documented — but not enforced — to be restricted to a safe charset. If an attacker sets `type_url` to a value containing `]`, `{`, `}`, or newline characters, that structural content is emitted verbatim into TextFormat output, which is often subsequently re-parsed by another component (logging pipelines, config snapshot/restore, textproto-based tooling).

### Finding Description
`Any.type_url`'s field comment states the additional restriction: content "must consist only of alphanumeric characters, percent-encoded escapes, and characters in the following set... `/-.~_!$&()*+,;=`" [1](#0-0) . This restriction is documentation-only: `internal::GetAnyFieldDescriptors()` only validates the field *types* (string/bytes), not their content [2](#0-1) , and neither the C++ setter, nor `Any::PackFrom`, nor binary/JSON parsing enforce the charset — a message merged from untrusted bytes can carry an arbitrary UTF-8 `type_url`.

`TextFormat::Printer::PrintAny()` then prints this unvalidated string raw: [3](#0-2) 

This is inconsistent with how ordinary string field values are printed elsewhere in the same file, where `ContainsCharactersToCEscape`/`CEscape` is applied to escape quotes, backslashes, and control characters before printing: [4](#0-3) 

Because `[` and `]` are structural delimiters in TextFormat's `Any` syntax (`[type_url] { ... }`, see the format description in `any.proto`) [5](#0-4) , an attacker-controlled `type_url` containing a `]` (optionally followed by additional text-format-looking content or a premature `}`) is written into the output stream as if it were legitimate structure, rather than as an opaque, escaped string. This mirrors the Netty defect: a value that is documented/expected to be constrained is written verbatim into a delimited text format by an encoder that assumes it is already "safe," while the actual value-setting path (parsing an `Any` from untrusted bytes) performs no such validation.

### Impact Explanation
If a consuming application prints untrusted `Any` messages via `TextFormat::PrintToString`/`Printer::PrintAny` with `SetExpandAny(true)` and later re-parses that text (e.g., debug-log replay tooling, textproto-based config generation from live data, or snapshot/restore systems that treat TextFormat as a round-trippable serialization), a malicious `type_url` could break out of the intended `[...]` field-name context and inject additional field/message structure into the reconstituted proto — a textproto structural-injection analogous to the HTTP request-line injection in the Netty CVE. Unlike the Netty case, this does not cross a network trust boundary by itself; impact depends entirely on whether the consuming application treats TextFormat output as re-parseable input, which is a real but non-default usage pattern.

### Likelihood Explanation
Reaching this requires (1) an application that accepts `Any` messages from an untrusted source via a public parse API, (2) that application prints them with `TextFormat::Printer` and `expand_any_ = true`, and (3) that the resulting text is subsequently fed back into `TextFormat::Parse`/`Merge`. This is a plausible but not universal pattern (common in debug-logging/snapshot tooling built around TextFormat). I was not able to fully trace the `Any` block's TextFormat *parser* logic in this pass to confirm exactly how it reacts to an embedded `]`/`{`/newline inside what should be the `type_url` token (e.g., whether it cleanly errors out vs. silently accepts trailing content as sibling fields), so the exact blast radius (parse error vs. successful field injection) is unverified.

### Recommendation
Escape or otherwise validate `type_url` before printing it inside `[...]` in `TextFormat::Printer::PrintAny()` (e.g., reject/escape characters outside the documented safe charset, or apply the same `ContainsCharactersToCEscape`/`CEscape`-style escaping used for ordinary string fields), and/or enforce the charset restriction on `type_url` at `Any` construction/merge time.

### Proof of Concept
Not fully executed/verified in this pass — I could not run code in this environment. Conceptually:
1. Craft an `Any` message via binary wire format with `type_url = "type.googleapis.com/x] extra_field: 1 #"` and `value` set to any valid serialized submessage bytes.
2. Parse this into an `Any` via the public `ParseFromString` API (no charset validation occurs).
3. Call `TextFormat::Printer().SetExpandAny(true).PrintToString(...)` on a wrapping message containing this `Any`.
4. Inspect the output: the injected `]`/text appears verbatim inside the `[...]` field-name position instead of being escaped, i.e., `[type.googleapis.com/x] extra_field: 1 #] { ... }`.
5. Feeding that output back into `TextFormat::Parse` would need to be verified against the actual `Any`-block parser to confirm whether it accepts the injected content as additional message fields — this step was not verified in this pass and should be validated by a follow-up session with code execution access.

**Uncertainty note:** Given the limits of static/read-only inspection here, I could not confirm the TextFormat parser's exact reaction to the injected delimiter, so I cannot claim a fully proven exploit chain — only a confirmed, code-level escaping omission in the encoder that structurally parallels the Netty root cause. A Devin session with terminal/build access would be needed to compile and run the PoC steps above to confirm downstream parse behavior and finalize severity.

### Citations

**File:** src/google/protobuf/any.proto (L69-71)
```text
// The text format representation of an `Any` is like a message with one field
// whose name is the type URL in brackets. For example, an `Any` containing a
// `foo.Bar` message may be written `[type.googleapis.com/foo.Bar] { a: 2 }`.
```

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

**File:** src/google/protobuf/any.cc (L44-59)
```text
bool GetAnyFieldDescriptors(const Message& message,
                            const FieldDescriptor * PROTOBUF_NULLABLE *
                                PROTOBUF_NONNULL type_url_field,
                            const FieldDescriptor * PROTOBUF_NULLABLE *
                                PROTOBUF_NONNULL value_field) {
  const Descriptor* descriptor = message.GetDescriptor();
  if (descriptor->full_name() != kAnyFullTypeName) {
    return false;
  }
  *type_url_field = descriptor->FindFieldByNumber(1);
  *value_field = descriptor->FindFieldByNumber(2);
  return (*type_url_field != nullptr &&
          (*type_url_field)->type() == FieldDescriptor::TYPE_STRING &&
          *value_field != nullptr &&
          (*value_field)->type() == FieldDescriptor::TYPE_BYTES);
}
```

**File:** src/google/protobuf/text_format.cc (L2227-2238)
```text
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

**File:** src/google/protobuf/text_format.cc (L2564-2566)
```text
  generator->PrintLiteral("[");
  generator->PrintString(type_url);
  generator->PrintLiteral("]");
```
