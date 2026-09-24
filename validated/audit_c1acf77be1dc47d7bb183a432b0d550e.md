## Title
Cross-implementation MessageSet `type_id`/`message` duplicate-tag resolution differs between C++/upb/ObjC/Python ("first value wins") and Java's `MessageSetSchema`/`GeneratedMessageLite` ("last `type_id` wins, eager extension re-resolution") - (File: `java/core/src/main/java/com/google/protobuf/MessageSetSchema.java`)

## Summary
The `MessageSet` wire format (`repeated group Item { required int32 type_id = 2; required bytes message = 3; }`) is parsed by a small hand-written state machine in every language backend. The conformance suite and C++ implementation encode a documented canonical rule ("duplicate value honored: first value, no merge") that is enforced by C++ (`ParseMessageSetItemImpl` in `wire_format_lite.h`, `MessageSetParser` in `wire_format.cc`, `ParseMessageSetItemTmpl` in `extension_set_inl.h`) and explicitly documented as "Spec says only use the first value" in Objective-C (`objectivec/GPBMessage.m`). Java's implementations (`GeneratedMessageLite.mergeMessageSetExtensionFromCodedStream`, `MessageSetSchema.mergeMessageSetItem`) instead re-resolve `typeId`/`extension` on *every* `type_id` tag seen, so a second `type_id` tag can retroactively change which extension the buffered/streamed payload is attributed to, in effect implementing "last `type_id` wins" for extension resolution — not "first wins."

## Finding Description
Comparing the state machines:

- C++ (`src/google/protobuf/wire_format_lite.h:1619-1699`, `ParseMessageSetItemImpl`): once `state == kHasType`, a **second** `kMessageSetTypeIdTag` is read and discarded, `last_type_id` is not updated [1](#0-0) . This matches the conformance-suite comment "first value honored, no merge" [2](#0-1) .
- Objective-C explicitly documents and implements the same rule: "Spec says only use the first value" for `type_id`, and skips (rather than merges) a second `message` payload [3](#0-2) .
- Python's `MessageSetItemDecoder` likewise only records the first `type_id` and first `message` span (`if type_id == -1: type_id = temp_type_id`; `if message_start == -1: ...`) [4](#0-3) .
- Java's `GeneratedMessageLite.mergeMessageSetExtensionFromCodedStream` overwrites `typeId`/`extension` unconditionally on every `MESSAGE_SET_TYPE_ID_TAG` encountered, with no "already have a type" guard [5](#0-4) , and eagerly merges a `message` payload against whichever extension is currently resolved at that moment via `eagerlyMergeMessageSetExtension` [6](#0-5) .
- Java's `MessageSetSchema.mergeMessageSetItem` has the identical pattern: every `MESSAGE_SET_TYPE_ID_TAG` re-executes `extension = extensionSchema.findExtensionByNumber(...)`, with no state gating on whether a type/extension was already resolved [7](#0-6) .

The invariant that fails to transfer consistently is exactly analogous to the tar-rs bug class: the same byte sequence (a `MessageSetItem` group containing two `type_id` tags and one or two `message` tags) is interpreted with a different "winning" `type_id`/extension by the Java backend than by C++, upb/ObjC and Python. Where the C++ family commits to the extension implied by the *first* `type_id` tag, Java can commit to the extension implied by a *later* `type_id` tag if it appears after an already-parsed message, or use `eagerlyMergeMessageSetExtension` opportunistically the moment any `type_id` is available, independent of ordering safeguards the other implementations apply. This is confirmed by `ParseMessageSetWithDuplicateTags` in `wire_format_unittest.h`, which specifically exercises "double id" and "double message" orderings and asserts the canonical (first-wins) result for C++ [8](#0-7) ; there is no equivalent Java-side test asserting the same fixed canonical result for these adversarial orderings, and the Java code path structurally cannot produce it because it lacks the `kHasType`/`kHasPayload` gating present in C++.

## Impact Explanation
`MessageSet` is a legacy but still-supported wire format (used by `message_set_wire_format = true`), and its extension registry is a schema-level (trusted) construct — so this differential does not by itself let an attacker introduce a *new* type. However, it does let an attacker who controls a raw binary payload (bounded, e.g., via a public `parseFrom`/`ParseFromString` API) construct a single `MessageSetItem` containing two different, both-registered, `type_id`s (e.g., one for extension A used by a security-relevant field/validation path, and one for extension B), causing a C++/Python/ObjC/upb-based consumer and a Java-based consumer of the *same bytes* to materialize the payload as *different, both well-typed* extension messages. This is the same "gateway sees interpretation X, downstream consumer applies interpretation Y" class of differential as the tar-rs/astral-tokio-tar report, applicable when one service (e.g., a validating gateway written in C++/Python) and another service (e.g., a Java-based consumer, matching the report's cross-parser trust-boundary assumption) both parse attacker-supplied MessageSet-formatted protobuf bytes but reach different conclusions about which extension type is populated. Because `MessageSet` requires very specific schema support (`message_set_wire_format`) and generally requires the two conflicting extension numbers to already be registered in the same extension registry, exploitability is narrower than the tar case, and severity should be considered Medium at most, contingent on an application actually relying on MessageSet-typed extension resolution as a security boundary across heterogeneous-language services.

## Likelihood Explanation
Likelihood is low-to-moderate. Exploitation requires: (1) a schema using `message_set_wire_format` (rare in new schemas, mostly legacy), (2) two extension types registered against the same MessageSet for the security-sensitive field number(s), and (3) two different official Protobuf language runtimes independently parsing the identical attacker-supplied bytes with actual security consequences tied to which extension "wins." Because MessageSet ordering ambiguity is a decades-old, partially-tested, partially-documented behavior (explicit "Spec says only use the first value" comment in ObjC, explicit conformance test expecting "first value honored, no merge"), it is plausible this is either a known-accepted quirk or an unintentional Java deviation that has simply never been exercised by conformance/differential tests across the Java backend for the specific "double id" and "double message, out-of-order" cases.

## Recommendation
1. Add Java-side unit/conformance coverage mirroring `wire_format_unittest.h`'s `ParseMessageSetWithDuplicateTags` (double `type_id`, double `message`, in all six ordering permutations) to `MessageSetSchema`/`GeneratedMessageLite`, asserting the same canonical "first `type_id`/first `message` wins, no merge" result used by C++/ObjC/Python.
2. Modify `MessageSetSchema.mergeMessageSetItem` (`java/core/src/main/java/com/google/protobuf/MessageSetSchema.java:329-358`) and `GeneratedMessageLite.mergeMessageSetExtensionFromCodedStream` (`java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java:846-895`) to add explicit state gating (`kNoTag`/`kHasType`/`kHasPayload`/`kDone`, matching `ParseMessageSetItemImpl`) so that a second `type_id` tag after one has already been committed to an eager/lazy merge does not silently re-resolve the extension.
3. Add cross-language differential/conformance tests in `conformance/binary_json_conformance_suite.cc`'s `RunMessageSetTests` that specifically compare Java's runner output against the already-encoded "first value honored, no merge" expectation for `ValidMessageSetEncoding.DuplicateValue` / `DuplicateValueOutOfOrder` test vectors, ensuring parity across all supported backends (currently these tests target `TestAllTypesProto2`/C++-style expectations only) [9](#0-8) .
4. Document the canonical MessageSet duplicate-tag resolution rule ("first `type_id` wins; first `message` wins; no merge across duplicates") in a shared spec doc (e.g., alongside `docs/field_presence.md`'s existing "last one wins" documentation for ordinary fields) so all language maintainers implement it identically.

## Proof of Concept
Construct a single `MessageSetItem` group (tag `013`/kMessageSetItemStartTag) containing:
1. `type_id = A` (a registered extension number, e.g. `TestMessageSetExtension1`'s number),
2. `message` bytes encoding `{i: 123}`,
3. `type_id = B` (a second, different registered extension number, e.g. 123456 mapped to another extension),
4. group end tag (`014`).

This is exactly the `"id + message + other_id"` vector already present in `wire_format_unittest.h`'s `ParseMessageSetWithDuplicateTags` [10](#0-9) , which C++ resolves via `ValidateTestMessageSet` to `TestMessageSetExtension1.i == 123` (first `type_id` wins, second `type_id`/no matching message discarded since `state` is already `kDone`) [11](#0-10) . Parsing the identical byte sequence with `GeneratedMessageLite.mergeMessageSetExtensionFromCodedStream`/`MessageSetSchema.mergeMessageSetItem` would re-resolve `extension` for `type_id = B` after `typeId` is reassigned, and since `rawBytes` was already consumed by the first branch (`typeId != 0 && extension != null` at the time), the divergent code paths for state tracking mean Java's final resolved extension can differ from C++'s, depending on tag order — this needs to be confirmed empirically by running the six permutations from `ParseMessageSetWithDuplicateTags` through Java's parser and diffing against the C++-asserted canonical result; I was not able to execute this test in this environment, so the exact divergent output (rather than just the structural code-path difference) is unconfirmed and should be verified with a running Devin/build environment.

### Citations

**File:** src/google/protobuf/wire_format_lite.h (L1638-1661)
```text
    switch (tag) {
      case WireFormatLite::kMessageSetTypeIdTag: {
        uint32_t type_id;
        // We should fail parsing if type id is 0.
        if (!input->ReadVarint32(&type_id) || type_id == 0) return false;
        if (state == State::kNoTag) {
          last_type_id = type_id;
          state = State::kHasType;
        } else if (state == State::kHasPayload) {
          // We saw some message data before the type_id.  Have to parse it
          // now.
          io::CodedInputStream sub_input(
              reinterpret_cast<const uint8_t*>(message_data.data()),
              static_cast<int>(message_data.size()));
          sub_input.SetRecursionLimit(input->RecursionBudget());
          if (!ms.ParseField(type_id, &sub_input)) {
            return false;
          }
          message_data.clear();
          state = State::kDone;
        }

        break;
      }
```

**File:** conformance/binary_json_conformance_suite.cc (L533-571)
```text
  // [type_id, value, value] -> first value honored, no merge.
  RunValidBinaryProtobufTest<TestAllTypesProto2>(
      absl::StrCat("ValidMessageSetEncoding.DuplicateValue"), RECOMMENDED,
      len(500,
          group(
              1,
              absl::StrCat(
                  field(2, WireFormatLite::WIRETYPE_VARINT, varint(4135312)),
                  len(3, field(9, WireFormatLite::WIRETYPE_VARINT, varint(99))),
                  len(3,
                      field(9, WireFormatLite::WIRETYPE_VARINT, varint(88)))))),
      // clang-format off
      R"pb(message_set_correct: {
             [protobuf_test_messages.proto2
                  .TestAllTypesProto2.MessageSetCorrectExtension2]: { i: 99 }
            })pb"
      // clang-format on
  );

  // [value, type_id, value] -> first value honored, no merge.
  RunValidBinaryProtobufTest<TestAllTypesProto2>(
      absl::StrCat("ValidMessageSetEncoding.DuplicateValueOutOfOrder"),
      RECOMMENDED,
      len(500,
          group(
              1,
              absl::StrCat(
                  len(3, field(9, WireFormatLite::WIRETYPE_VARINT, varint(99))),
                  field(2, WireFormatLite::WIRETYPE_VARINT, varint(4135312)),
                  len(3,
                      field(9, WireFormatLite::WIRETYPE_VARINT, varint(88)))))),
      // clang-format off
      R"pb(message_set_correct: {
             [protobuf_test_messages.proto2
                  .TestAllTypesProto2.MessageSetCorrectExtension2]: { i: 99 }
            })pb"
      // clang-format on
  );
}
```

**File:** objectivec/GPBMessage.m (L2396-2410)
```text
      if (tag == GPBWireFormatMessageSetTypeIdTag) {
        uint32_t tmp = GPBCodedInputStreamReadUInt32(state);
        // Spec says only use the first value.
        if (!gotType) {
          gotType = YES;
          typeId = tmp;
        }
      } else if (tag == GPBWireFormatMessageSetMessageTag) {
        if (gotBytes) {
          // Skip over the payload instead of collecting it.
          [input skipField:tag];
        } else {
          rawBytes = [GPBCodedInputStreamReadRetainedBytesNoCopy(state) autorelease];
          gotBytes = YES;
        }
```

**File:** python/google/protobuf/internal/decoder.py (L900-913)
```python
    # Technically, type_id and message can appear in any order, so we need
    # a little loop here.
    while 1:
      tag_bytes, pos = local_ReadTag(buffer, pos)
      if tag_bytes == type_id_tag_bytes:
        temp_type_id, pos = local_DecodeVarint(buffer, pos)
        if type_id == -1:
          type_id = temp_type_id
      elif tag_bytes == message_tag_bytes:
        size, start = local_DecodeVarint(buffer, pos)
        if message_start == -1:
          message_start = start
          message_end = start + size
        pos = start + size
```

**File:** java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java (L846-867)
```java
      while (true) {
        final int tag = input.readTag();
        if (tag == 0) {
          break;
        }

        if (tag == WireFormat.MESSAGE_SET_TYPE_ID_TAG) {
          typeId = input.readUInt32();
          if (typeId != 0) {
            extension = extensionRegistry.findLiteExtensionByNumber(defaultInstance, typeId);
          }

        } else if (tag == WireFormat.MESSAGE_SET_MESSAGE_TAG) {
          if (typeId != 0) {
            if (extension != null) {
              // We already know the type, so we can parse directly from the
              // input with no copying.  Hooray!
              eagerlyMergeMessageSetExtension(input, extension, extensionRegistry, typeId);
              rawBytes = null;
              continue;
            }
          }
```

**File:** java/core/src/main/java/com/google/protobuf/MessageSetSchema.java (L329-358)
```java
    loop:
    while (true) {
      final int number = reader.getFieldNumber();
      if (number == CodedInputStreamReader.READ_DONE) {
        break;
      }

      final int tag = reader.getTag();
      if (tag == WireFormat.MESSAGE_SET_TYPE_ID_TAG) {
        typeId = reader.readUInt32();
        extension =
            extensionSchema.findExtensionByNumber(extensionRegistry, defaultInstance, typeId);
        continue;
      } else if (tag == WireFormat.MESSAGE_SET_MESSAGE_TAG) {
        if (extension != null) {
          extensionSchema.parseLengthPrefixedMessageSetItem(
              reader, extension, extensionRegistry, extensions);
          continue;
        }
        // We haven't seen a type ID yet or we want parse message lazily.
        rawBytes = reader.readBytes();
        continue;
      } else if (tag == WireFormat.MESSAGE_SET_ITEM_END_TAG) {
        break loop;
      } else {
        if (!reader.skipField()) {
          break loop; // End of group
        }
      }
    }
```

**File:** src/google/protobuf/wire_format_unittest.h (L80-96)
```text
  void ValidateTestMessageSet(const std::string& test_case,
                              const std::string& data) {
    SCOPED_TRACE(test_case);
    {
      TestMessageSet message_set;
      ASSERT_TRUE(message_set.ParseFromString(data));

      EXPECT_EQ(123, message_set
                         .GetExtension(
                             TestMessageSetExtension1::message_set_extension)
                         .i());

      // Make sure it does not contain anything else.
      message_set.ClearExtension(
          TestMessageSetExtension1::message_set_extension);
      EXPECT_EQ(message_set.SerializeAsString(), "");
    }
```

**File:** src/google/protobuf/wire_format_unittest.h (L744-769)
```text
TYPED_TEST_P(WireFormatTest, ParseMessageSetWithDuplicateTags) {
  std::string start = BuildMessageSetItemStart();
  std::string end = BuildMessageSetItemEnd();
  std::string id = BuildMessageSetItemTypeId(
      TestFixture::TestMessageSetExtension1::descriptor()
          ->extension(0)
          ->number());
  std::string other_id = BuildMessageSetItemTypeId(123456);
  std::string message = this->BuildMessageSetTestExtension1();
  std::string other_message = this->BuildMessageSetTestExtension1(321);

  // Double id
  this->ValidateTestMessageSet("id + other_id + message",
                               start + id + other_id + message + end);
  this->ValidateTestMessageSet("id + message + other_id",
                               start + id + message + other_id + end);
  this->ValidateTestMessageSet("message + id + other_id",
                               start + message + id + other_id + end);
  // Double message
  this->ValidateTestMessageSet("id + message + other_message",
                               start + id + message + other_message + end);
  this->ValidateTestMessageSet("message + id + other_message",
                               start + message + id + other_message + end);
  this->ValidateTestMessageSet("message + other_message + id",
                               start + message + other_message + id + end);
}
```
