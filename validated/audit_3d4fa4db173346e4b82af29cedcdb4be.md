### Title
MessageSet type_id/message duplicate-tag semantics diverge across Protobuf language runtimes, enabling cross-parser interpretation smuggling - ([File: java/core/src/main/java/com/google/protobuf/MessageReflection.java])

### Summary
The Squid CVE's failed invariant is: a control field that determines how the remainder of a message is segmented (`Transfer-Encoding`) is validated with an ad-hoc substring test rather than a strict, single-source-of-truth parse, so two different consumers of the same bytes (the cache and the origin) disagree about where one message ends and the next begins — enabling a second, attacker-chosen interpretation to be smuggled through. The Protobuf analog is the `MessageSet` extension wire item (`group Item { required uint32 typeId = 2; required bytes message = 3; }`), whose `type_id`/`message` fields may legally repeat or arrive out of order. Different official Protobuf runtimes resolve duplicate `typeId`/`message` occurrences with **different, undocumented-as-a-cross-runtime-contract policies** (first-wins vs. last-wins vs. merge), so the same bounded, well-formed `MessageSet` byte string parses to different extension values depending on which runtime consumes it.

### Finding Description
The `MessageSet` group format allows `type_id` (tag 2) and `message` (tag 3) to appear in any order and, per the field comments, "in theory" more than once. Each runtime implements its own disambiguation policy for duplicates:

- C++ (`ParseMessageSetItemImpl`) locks in the **first** `type_id` seen (`state == State::kNoTag` branch sets `last_type_id` only once) and, for `type_id` arriving after payload, parses using the type_id value **at the time the type_id tag is finally seen** — i.e. first-`message`, first-committed-`type_id` wins: [1](#0-0) 

- upb (`upb_Decoder_DecodeMessageSetItem`) explicitly ignores duplicate `type_id` and duplicate `message` tags (`// Ignore dup.`), i.e. strict first-wins for both fields: [2](#0-1) 

- Objective-C (`GPBMessage.m parseMessageSet:`) also explicitly implements first-wins ("Spec says only use the first value") for `type_id`, and skips (drops) any subsequent `message` payloads once one has been captured: [3](#0-2) 

- Python (`decoder.py MessageSetItemDecoder`) is first-wins for both `type_id` (`if type_id == -1: type_id = temp_type_id`) and `message` (`if message_start == -1: ...`): [4](#0-3) 

- Java (`MessageReflection.mergeMessageSetExtensionFromCodedStream` and `GeneratedMessageLite`'s lite equivalent, and `MessageSetSchema` for the Lite/gencode path) **unconditionally overwrites** `typeId` every time the `MESSAGE_SET_TYPE_ID_TAG` is seen, with no "already have one" guard: [5](#0-4) [6](#0-5) 
and, more importantly, when `extension != null` and a `MESSAGE_SET_MESSAGE_TAG` is seen it eagerly `parseLengthPrefixedMessageSetItem`s / `eagerlyMergeMessageSetExtension`s **directly into the same extension entry** for every occurrence (i.e., it merges repeated `message` occurrences into a single field builder rather than dropping duplicates), a semantically different behavior from the "first wins, drop rest" policy of C++/upb/ObjC/Python: [7](#0-6) [8](#0-7) 

This is exactly the Squid failure pattern transplanted to Protobuf: the wire spec permits an ambiguous, repeatable control token (`type_id`) governing how bytes downstream (`message`) are attributed, and the "authoritative interpretation" of that ambiguity is not defined by a single canonical grammar rule enforced identically everywhere — it is answered ad hoc, per-implementation. The upstream conformance suite itself documents and locks in the *intended* canonical answer ("first value/first type_id and value honored, no merge") as `RECOMMENDED` (not `REQUIRED`) tests: [9](#0-8) 
which acknowledges this is a known area of behavioral divergence, but Java's non-lazy/extension-known path is not exercised by those specific "no merge" assertions against a *merging* consumer, so the divergence documented here (merge-on-duplicate-message in Java's known-extension fast path vs. first-wins-drop in C++/upb/ObjC/Python) is not fully pinned down by the existing conformance assertions I was able to inspect.

### Impact Explanation
The consuming-application exposure model here mirrors Squid's: a bounded, attacker-controlled `MessageSet`-wire-format byte string is legitimately accepted by one Protobuf-consuming service/tier (e.g., a Java front-end validator or router using `MessageReflection`/`MessageSetSchema`) and the same bytes (or a re-serialized/forwarded/cached copy) are subsequently parsed by a different tier or a different-language backend (e.g., a C++ or upb-based service) that applies a different duplicate-resolution rule. Because a repeated `type_id`/`message` pair is syntactically valid, a single payload can be crafted so that the "front" parser extracts extension value A (e.g., for authorization/routing decisions) while the "back" parser, given the identical bytes, extracts extension value B (e.g., the actual business payload) — a data-smuggling/confused-deputy condition analogous to Squid's cache poisoning via divergent chunked-encoding interpretation between the cache and the origin. This is a logic/integrity issue (attacker can make one component "see" different extension content than another sees from the same bytes), not a memory-safety bug, and its blast radius depends entirely on what security-relevant decision the first parser makes based on the value it picked.

### Likelihood Explanation
`MessageSet` wire format is a legacy/rarely-used proto2 feature (`option message_set_wire_format = true`), so the number of real production consumers that (a) use MessageSet extensions, and (b) pass the same wire bytes between heterogeneous Protobuf language runtimes without re-normalizing, is limited. However, constructing the duplicate-tag payload requires no special privileges — it is an ordinary, well-formed, bounded binary payload through the public `ParseFrom`/`MergeFrom` API, matching the required attacker model exactly. Likelihood is Medium/Low-Medium: plausible in multi-language microservice pipelines that forward raw serialized MessageSet-wire-format extensions between a Java tier and a C++/upb/Python/ObjC tier, but not applicable to typical proto3 or non-MessageSet-extension usage.

### Recommendation
- Normalize `MessageSet` `type_id`/`message` duplicate-resolution policy to a single, explicitly documented rule (first-wins, matching upb/C++/ObjC/Python) and align Java's `MessageSetSchema`/`MessageReflection`/`GeneratedMessageLite` eager-merge-on-known-extension code path to reject or drop (not merge) subsequent `message` occurrences once one has already been consumed for a given `type_id`.
- Promote the existing `RECOMMENDED` conformance tests (`ValidMessageSetEncoding.DuplicateValue`, `DuplicateDifferentTypeId`, `DuplicateValueOutOfOrder`) to `REQUIRED` and add Java-specific fast-path (`MessageSetSchema`, known-extension eager merge) coverage, since the current suite does not clearly pin the eager-merge behavior observed in `MessageSetSchema.java`.
- Where applications forward raw serialized `MessageSet`-wire-format bytes between heterogeneous-language services, either re-serialize/canonicalize after parsing in the first tier (never forward raw untrusted bytes) or reject messages containing duplicate `type_id`/`message` tags outright.

### Proof of Concept
Construct a `MessageSet` item group (`start := kMessageSetItemStartTag`) containing, in order:
1. `type_id` tag = extension A's field number
2. `message` tag = length-prefixed payload P1 (valid serialization of extension A's message type)
3. `message` tag = length-prefixed payload P2 (a second valid serialization for the same extension A's message type, with different field values)
4. `end := kMessageSetItemEndTag`

Feeding this single, bounded, well-formed byte string to:
- `upb`, C++ (`ParseMessageSetItemImpl`), ObjC, or Python: extension A resolves to the **first** payload's content (P1), and the duplicate `message` tag is dropped/ignored (as shown by the `// Ignore dup.` logic and first-wins loop conditions cited above).
- Java's `MessageSetSchema.mergeOneFieldFrom` known-extension path: because `parseLengthPrefixedMessageSetItem` is invoked unconditionally for every `MESSAGE_SET_MESSAGE_TAG` occurrence when `extension != null` (no "already parsed" guard is present in the loop shown in `MessageSetSchema.java` lines 336-358), the two payloads are merged field-by-field into the same extension entry, producing a result that is the **field-wise merge of P1 and P2**, not simply P1.

This demonstrates that the same bounded, trusted-schema, attacker-controlled bytes yield **three distinct outcomes** (first-wins/P1, drop-duplicate, or merge-of-P1-and-P2) depending solely on which official runtime performs the parse — the same "differing message-boundary interpretation of ambiguous control data across two consumers of one byte stream" root cause as the Squid HTTP Request Splitting CVE. I was not able to execute this PoC in this environment (no code execution tool available); the divergence is established by direct comparison of the cited parsing logic across the C++, upb, ObjC, Python, and Java sources, not by running assertions.

### Citations

**File:** src/google/protobuf/wire_format_lite.h (L1639-1658)
```text
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
```

**File:** upb/wire/decode.c (L679-712)
```c
      case kEndItemTag:
        return ptr;
      case kTypeIdTag: {
        uint64_t tmp;
        ptr = upb_WireReader_ReadVarint(ptr, &tmp, EPS(d));
        if (state_mask & kUpb_HaveId) break;  // Ignore dup.
        state_mask |= kUpb_HaveId;
        type_id = tmp;
        if (state_mask & kUpb_HavePayload) {
          upb_Decoder_AddMessageSetItem(d, msg, layout, type_id, preserved.data,
                                        preserved.size);
        }
        break;
      }
      case kMessageTag: {
        uint32_t size;
        upb_StringView sv;
        ptr = upb_Decoder_DecodeSize(d, ptr, &size);
        ptr = upb_EpsCopyInputStream_ReadStringAlwaysAlias(&d->input, ptr, size,
                                                           &sv);
        if (!ptr) {
          upb_ErrorHandler_ThrowError(d->err, kUpb_DecodeStatus_Malformed);
        }
        if (state_mask & kUpb_HavePayload) break;  // Ignore dup.
        state_mask |= kUpb_HavePayload;
        if (state_mask & kUpb_HaveId) {
          upb_Decoder_AddMessageSetItem(d, msg, layout, type_id, sv.data,
                                        sv.size);
        } else {
          // Out of order, we must preserve the payload.
          preserved = sv;
        }
        break;
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

**File:** java/core/src/main/java/com/google/protobuf/MessageReflection.java (L1359-1371)
```java
      if (tag == WireFormat.MESSAGE_SET_TYPE_ID_TAG) {
        typeId = input.readUInt32();
        if (typeId != 0) {
          // extensionRegistry may be either ExtensionRegistry or
          // ExtensionRegistryLite. Since the type we are parsing is a full
          // message, only a full ExtensionRegistry could possibly contain
          // extensions of it. Otherwise we will treat the registry as if it
          // were empty.
          if (extensionRegistry instanceof ExtensionRegistry) {
            extension =
                target.findExtensionByNumber((ExtensionRegistry) extensionRegistry, type, typeId);
          }
        }
```

**File:** java/core/src/main/java/com/google/protobuf/MessageSchema.java (L337-341)
```java
      next = info.charAt(i++);
      if (next >= 0xD800) {
        int result = next & 0x1FFF;
        int shift = 13;
        while ((next = info.charAt(i++)) >= 0xD800) {
```

**File:** java/core/src/main/java/com/google/protobuf/MessageSetSchema.java (L336-358)
```java
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

**File:** java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java (L852-876)
```java
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
          // We haven't seen a type ID yet or we want parse message lazily.
          rawBytes = input.readBytes();

        } else { // Unknown tag. Skip it.
          if (!input.skipField(tag)) {
            break; // End of group
          }
        }
      }
```

**File:** conformance/binary_json_conformance_suite.cc (L514-550)
```text
  // [type_id, value, type_id (different)] -> first type_id and value honored.
  RunValidBinaryProtobufTest<TestAllTypesProto2>(
      absl::StrCat("ValidMessageSetEncoding.DuplicateDifferentTypeId"),
      RECOMMENDED,
      len(500,
          group(
              1,
              absl::StrCat(
                  field(2, WireFormatLite::WIRETYPE_VARINT, varint(4135312)),
                  len(3, field(9, WireFormatLite::WIRETYPE_VARINT, varint(99))),
                  field(2, WireFormatLite::WIRETYPE_VARINT, varint(1547769))))),
      // clang-format off
      R"pb(message_set_correct: {
             [protobuf_test_messages.proto2
                  .TestAllTypesProto2.MessageSetCorrectExtension2]: { i: 99 }
            })pb"
      // clang-format on
  );

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
```
