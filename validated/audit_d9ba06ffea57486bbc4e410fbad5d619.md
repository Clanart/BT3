### Title
Unbounded Recursion via Nested `Any` Messages in C++ ProtoJSON Serialization Writer - (File: src/google/protobuf/json/internal/unparser.cc)

### Summary
The TensorFlow Serving CVE-2025-0649 describes unbounded recursion when *stringifying* (serializing) a value to JSON, causing stack exhaustion. The strongest analog in this repo is the C++ ProtoJSON serialization path (`MessageToJsonStream` / `BinaryToJsonStream` in `src/google/protobuf/json/internal/unparser.cc`), which converts an in-memory/binary `google.protobuf.Any`-bearing message to JSON via a set of mutually-recursive `WriteMessage`/`WriteAny`/`WriteFields`/`WriteSingular` functions. Unlike the JSON *parsers* in every other language (C#, Java, Python — all of which have explicit, documented "Any-in-Any" recursion-limit regression fixes, e.g. `MaliciousRecursionOfAnyInAny`), the C++ ProtoJSON **writer** contains no recursion-depth counter at all.

### Finding Description
`WriteAny<Traits>` in `src/google/protobuf/json/internal/unparser.cc` (lines 769–835) handles a `google.protobuf.Any` field by:
1. Extracting the raw `value` bytes field of the `Any`.
2. Calling `Traits::WithDecodedMessage(any_desc, any_bytes, ...)`, which **parses the opaque bytes on the fly** into a submessage.
3. Recursing into `WriteMessage<Traits>(writer, unerased, any_desc)` on the freshly-decoded submessage. [1](#0-0) 

`WriteMessage` is explicitly documented as "mutually recursive" with `WriteSingular`/`WriteField`/`WriteFields`/`WriteAny`: [2](#0-1) 

The only depth-related state in the writer, `JsonWriter::Push()`/`Pop()`, only tracks pretty-print indentation — it is not a recursion-limit guard: [3](#0-2) 

Grepping `unparser.h` and `writer.h` confirms there is no `recursion_depth`/`max_depth`/`kMaxDepth` field or check anywhere in the JSON writer implementation, in contrast to the JSON *lexer* (parser side), which explicitly implements `Push()`/`Pop()` with a `recursion_depth` counter: [4](#0-3) 

**Why this transfers the TensorFlow invariant**: the external bug's invariant — "recursive stringification of an attacker-influenced structure must be depth-bounded" — is exactly what every other public entry point in this repo enforces for JSON *and* the binary `Any`-of-`Any` case (see the C#/Java/Python regression tests below), but it is missing from the C++ *writer*.

**Why the top-level binary parse doesn't already bound this**: `Any.value` is declared as a plain `bytes` field. When the outer container message is parsed via `ParseFromString`/`ParseFromCodedStream`, the standard message-recursion-limit machinery (`CodedInputStream`, default limit ~100, enforced per nested `Message`) only counts recursion through actual embedded `Message`/`Group` fields. A chain of `Any` messages, each holding the next `Any` serialized into its `value` bytes field, is *not* nested at the wire level — from the binary parser's point of view it's just one flat message with one `bytes` field. Arbitrary "Any-of-Any-of-Any…" depth can therefore be constructed with a payload whose *wire-level* nesting is only 1, defeating the top-level parse recursion limit. Each `Any` is only decoded into a real submessage lazily, on demand, at JSON-serialization time by `WithDecodedMessage`, and that per-`Any` decode call does not inherit or check any global recursion budget — mirroring precisely the exact bug that was patched in the C# tokenizer:

> "Previously the `JsonReplayTokenizer` constructed for each `Any` body started at depth zero, allowing the [recursion] limit to be bypassed and producing an uncatchable `StackOverflowException`." [5](#0-4) 

The equivalent Java (`mergeAny`, checked against `currentDepth`/`recursionLimit`) and Python (`ConvertMessage`, checked against `recursion_depth`/`max_recursion_depth`) fixes are documented and tested: [6](#0-5) [7](#0-6) 

For the C++ **binary→JSON** path, an unrelated attempt to bound recursion exists for deeply nested `TYPE_GROUP` fields (rejected at 200 levels, `JsonTest.DeeplyNestedGroupsRejected`), which is a *wire-level* group nesting check performed during `UntypedMessage::ParseFromStream` — not a JSON-writer-level check, and it does not cover the `Any`-of-`Any` bypass described above: [8](#0-7) 

I was unable to locate the implementation of `UntypedMessage::ParseFromStream`/`Traits::WithDecodedMessage` (used by `WriteAny`) in the indexed portion of the codebase to confirm definitively whether it applies a fresh, unguarded recursion limit per `Any` layer or whether some depth-propagation exists that I could not find via search. This is a limitation of the current investigation — a background Devin session with full filesystem access would be needed to inspect `descriptor_traits.h`'s `WithDecodedMessage` implementation and `UntypedMessage::ParseFromStream` directly to fully confirm the absence of any hidden global depth counter.

### Impact Explanation
If confirmed, an ordinary client that can get a bounded, deeply `Any`-of-`Any` nested binary protobuf message re-serialized to JSON via a public API (`google::protobuf::util::MessageToJsonString`/`MessageToJsonStream`, or the `BinaryToJsonStream` type-resolver path used by JSON/gRPC transcoding gateways) could trigger unbounded native-stack recursion in the C++ writer, causing a process crash (stack-overflow / SIGSEGV) — a Denial of Service against any consuming server process performing binary-to-JSON or message-to-JSON serialization. Impact is limited to availability, matching the CVSS profile of the reported TensorFlow issue (VA:H, no confidentiality/integrity impact), consistent with a Medium severity, not Critical/High, since no memory corruption or RCE is implied — only a crash.

### Likelihood Explanation
Likelihood is Medium: constructing a small binary payload with dozens/hundreds of nested `Any` wrapper layers is trivial and requires no special privileges — just an ordinary bounded protobuf message sent through any public serialization API that ends up calling `MessageToJsonStream`/`BinaryToJsonStream`. The only barrier is that the consuming application must expose message-to-JSON (or binary-to-JSON) conversion of attacker-influenced `Any` fields, which is a common pattern (e.g., gRPC-JSON transcoding gateways, logging/debug endpoints that dump proto messages as JSON).

### Recommendation
Add an explicit recursion-depth counter to `JsonWriter`/`WriterOptions` (mirroring the JSON lexer's existing `Push()`/`Pop()` `recursion_depth` guard in `lexer.h`), and thread it through `WriteMessage`/`WriteAny` so that each level of message nesting — including each layer of `Any`-of-`Any` decoded lazily by `WithDecodedMessage` — increments/decrements a bounded counter and returns an `InvalidArgumentError`/similar status once a maximum depth (e.g., 100, matching `CodedInputStream::DefaultRecursionLimit`) is exceeded, instead of recursing unboundedly.

### Proof of Concept
Conceptual construction (not run against the live binary due to tool limitations in this investigation):
1. Build an innermost message `M0` (e.g., an empty `Any` or trivial `Struct`).
2. Iteratively wrap: `A_i.value = A_{i-1}.SerializeAsString(); A_i.type_url = "type.googleapis.com/google.protobuf.Any"` for `i = 1..N` with `N` in the tens of thousands.
3. Because each `A_i` is a flat message with a `bytes` field, `A_N.SerializeAsString()` parses trivially under the default binary recursion limit (wire-level nesting is 1, not `N`).
4. Call `google::protobuf::util::MessageToJsonString(A_N, &json_out)` (or route `A_N`'s bytes through `BinaryToJsonStream` with the `Any` type URL). Each layer's `Any.value` is decoded and immediately recursed into by `WriteAny`→`WriteMessage`→...→`WriteAny`, with no depth counter, expected to exhaust the native call stack once `N` is large enough (analogous to the C#/Java/Python-documented `MaliciousRecursionOfAnyInAny` regression, adapted to the serialization/writer direction rather than the parsing direction).

Because I could not execute this against the actual build in this ask-only investigation, this should be validated by a Devin session with code execution to (a) confirm `WithDecodedMessage`'s implementation has no depth propagation, and (b) empirically reproduce the stack overflow with a concrete `N`.

### Citations

**File:** src/google/protobuf/json/internal/unparser.cc (L115-118)
```text
// Mutually recursive with functions that follow.
template <typename Traits>
absl::Status WriteMessage(JsonWriter& writer, const Msg<Traits>& msg,
                          const Desc<Traits>& desc, bool is_top_level = false);
```

**File:** src/google/protobuf/json/internal/unparser.cc (L794-834)
```text
  return Traits::WithDynamicType(
      desc, std::string(*type_url),
      [&](const Desc<Traits>& any_desc) -> absl::Status {
        absl::string_view any_bytes;
        if (has_value) {
          absl::StatusOr<absl::string_view> bytes =
              Traits::GetString(value_field, writer.ScratchBuf(), msg);
          RETURN_IF_ERROR(bytes.status());
          any_bytes = *bytes;
        }

        return Traits::WithDecodedMessage(
            any_desc, any_bytes,
            [&](const Msg<Traits>& unerased) -> absl::Status {
              bool first = false;
              if (ClassifyMessage(Traits::TypeName(any_desc)) !=
                  MessageType::kNotWellKnown) {
                if (ClassifyMessage(Traits::TypeName(any_desc)) ==
                        MessageType::kValue &&
                    IsEmpty<Traits>(unerased, any_desc)) {
                  // Omit "value" field for empty Value.
                } else {
                  writer.WriteComma(first);
                  writer.NewLine();
                  writer.Write("\"value\":");
                  writer.Whitespace(" ");
                  RETURN_IF_ERROR(
                      WriteMessage<Traits>(writer, unerased, any_desc));
                }
              } else {
                RETURN_IF_ERROR(
                    WriteFields<Traits>(writer, unerased, any_desc, first));
              }
              writer.Pop();
              if (!first) {
                writer.NewLine();
              }
              writer.Write("}");
              return absl::OkStatus();
            });
      });
```

**File:** src/google/protobuf/json/internal/writer.h (L90-91)
```text
  void Push() { ++indent_; }
  void Pop() { --indent_; }
```

**File:** src/google/protobuf/json/internal/lexer.h (L226-234)
```text
  absl::Status Push() {
    if (options_.recursion_depth == 0) {
      return Invalid("JSON content was too deeply nested");
    }
    --options_.recursion_depth;
    return absl::OkStatus();
  }

  void Pop() { ++options_.recursion_depth; }
```

**File:** csharp/src/Google.Protobuf.Test/JsonParserTest.cs (L946-973)
```csharp
        /// <summary>
        /// Regression test: deeply-nested google.protobuf.Any payloads must
        /// honor JsonParser.Settings.RecursionLimit. Previously the
        /// JsonReplayTokenizer constructed for each Any body started at depth
        /// zero, allowing the limit to be bypassed and producing an
        /// uncatchable StackOverflowException. See the equivalent fixes in
        /// Java (mergeAnyMessage) and Python (_ConvertAnyMessage).
        /// </summary>
        [Test]
        public void MaliciousRecursionOfAnyInAny()
        {
            int depth = 100;
            const string anyHeader = "{\"@type\":\"type.googleapis.com/google.protobuf.Any\",\"value\":";
            string json =
                string.Concat(Enumerable.Repeat(anyHeader, depth)) +
                "{}" +
                new string('}', depth);

            var registry = TypeRegistry.FromMessages(Any.Descriptor);

            // A generous limit must still successfully parse the document.
            var sufficientLimitParser = new JsonParser(new JsonParser.Settings(depth * 2, registry));
            Assert.DoesNotThrow(() => sufficientLimitParser.Parse<Any>(json));

            // A limit smaller than the nesting depth must throw a recoverable
            // protobuf exception rather than overflowing the stack.
            var insufficientLimitParser = new JsonParser(new JsonParser.Settings(10, registry));
            Assert.Throws<InvalidProtocolBufferException>(() => insufficientLimitParser.Parse<Any>(json));
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1919-1936)
```java
      Message.Builder contentBuilder =
          DynamicMessage.getDefaultInstance(contentType).newBuilderForType();
      WellKnownTypeParser specialParser = wellKnownTypeParsers.get(contentType.getFullName());

      if (currentDepth >= recursionLimit) {
        throw new InvalidProtocolBufferException("Hit recursion limit.");
      }
      ++currentDepth;
      if (specialParser != null) {
        JsonElement value = object.get("value");
        if (value != null) {
          specialParser.merge(this, value, contentBuilder);
        }
      } else {
        mergeMessage(json, contentBuilder, true);
      }
      --currentDepth;
      builder.setField(valueField, contentBuilder.build().toByteString());
```

**File:** python/google/protobuf/json_format.py (L568-589)
```python
    # Increment recursion depth at message entry. The max_recursion_depth limit
    # is exclusive: a depth value equal to max_recursion_depth will trigger an
    # error. For example, with max_recursion_depth=5, nesting up to depth 4 is
    # allowed, but attempting depth 5 raises ParseError.
    self.recursion_depth += 1
    if self.recursion_depth > self.max_recursion_depth:
      raise ParseError(
          'Message too deep. Max recursion depth is {0}'.format(
              self.max_recursion_depth
          )
      )
    message_descriptor = message.DESCRIPTOR
    full_name = message_descriptor.full_name
    if not path:
      path = message_descriptor.name
    if _IsWrapperMessage(message_descriptor):
      self._ConvertWrapperMessage(value, message, path)
    elif full_name in _WKTJSONMETHODS:
      methodcaller(_WKTJSONMETHODS[full_name][1], value, message, path)(self)
    else:
      self._ConvertFieldValuePair(value, message, path)
    self.recursion_depth -= 1
```

**File:** src/google/protobuf/json/json_test.cc (L1622-1666)
```text
TEST_P(JsonTest, DeeplyNestedGroupsRejected) {
  // Verify that deeply nested TYPE_GROUP fields are rejected with an error
  // rather than causing unbounded stack recursion.
  FileDescriptorProto file_proto;
  file_proto.set_name("group_depth_test.proto");
  file_proto.set_syntax("proto2");

  auto* msg = file_proto.add_message_type();
  msg->set_name("RecursiveGroup");

  auto* nested = msg->add_nested_type();
  nested->set_name("Nested");

  auto* inner = nested->add_field();
  inner->set_name("nested");
  inner->set_number(1);
  inner->set_type(FieldDescriptorProto::TYPE_GROUP);
  inner->set_type_name("Nested");
  inner->set_label(FieldDescriptorProto::LABEL_OPTIONAL);

  auto* outer = msg->add_field();
  outer->set_name("nested");
  outer->set_number(1);
  outer->set_type(FieldDescriptorProto::TYPE_GROUP);
  outer->set_type_name("Nested");
  outer->set_label(FieldDescriptorProto::LABEL_OPTIONAL);

  DescriptorPool pool;
  const FileDescriptor* fd = pool.BuildFile(file_proto);
  ASSERT_NE(fd, nullptr);

  std::unique_ptr<TypeResolver> resolver(
      google::protobuf::util::NewTypeResolverForDescriptorPool("type.googleapis.com",
                                                     &pool));

  // 200 nested groups: 200x START_GROUP(field=1) + 200x END_GROUP(field=1)
  // Field 1, wire type 3 = 0x0B; Field 1, wire type 4 = 0x0C
  std::string payload(200, 0x0B);
  payload.append(200, 0x0C);

  std::string out;
  absl::Status s = BinaryToJsonString(
      resolver.get(), "type.googleapis.com/RecursiveGroup", payload, &out);
  EXPECT_THAT(s, StatusIs(absl::StatusCode::kInvalidArgument));
}
```
