Based on extensive searching, I could not identify a genuine Protobuf analog to this report's core defect.

The bridge-service bug is a **fail-open pattern**: a resource-discovery loop (port scanning) that, on total failure, still returns success and uses an attacker-exploitable default (port 8080) rather than aborting — letting an untrusted party supply content that the victim then treats as trusted (a JS bundle deriving private keys).

I searched for the Protobuf-surface analog of this exact invariant — a security-relevant fallback/lookup (extension number allocation, `Any`/type-URL resolution, closed-enum default resolution, recursion-limit bookkeeping, JSON well-known-type dispatch) that could *silently succeed* with an attacker-influenced or unsafe default when the correct behavior is to reject the input. In every candidate location, the code fails closed, not open:

- `Any` type-URL resolution in Java's `JsonFormat.mergeAny`/`printAny` throws `InvalidProtocolBufferException` when the type cannot be resolved rather than proceeding with a wrong/default type. [1](#0-0) 
- Closed-enum defaults (`CrossLinkField`) use the schema's own first declared value as a default — not attacker-controlled wire data — and out-of-range values on the wire are routed to unknown fields, never accepted as if valid. [2](#0-1) [3](#0-2) 
- Recursion-depth/limit bookkeeping in C++ (`ParseContext`), C# (`ParsingPrimitivesMessages`), and Objective-C (`GPBCodedInputStream`) all check-then-fail rather than defaulting to "allowed" when the check can't be performed. [4](#0-3) [5](#0-4) [6](#0-5) 
- Extension-number/field-number collision handling in `DescriptorBuilder::CrossLinkField` is compile-time schema validation (trusted `.proto` input), not runtime parsing of attacker-controlled bytes, so it falls outside the client-attacker threat model set by the rules.
<invoke name="grep_search">
<parameter name="pattern">TODO: Change this to an error</parameter>
<parameter name="repos">["Kirstentat/protobuf--024"]</parameter>
</invoke>

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1911-1917)
```java
      Descriptor contentType = registry.getDescriptorForTypeUrl(typeUrl);
      if (contentType == null) {
        contentType = oldRegistry.getDescriptorForTypeUrl(typeUrl);
        if (contentType == null) {
          throw new InvalidProtocolBufferException("Cannot resolve type: " + typeUrl);
        }
      }
```

**File:** src/google/protobuf/descriptor.cc (L7455-7460)
```text
      } else if (field->enum_type()->value_count() > 0) {
        // All enums must have at least one value, or we would have reported
        // an error elsewhere.  We use the first defined value as the default
        // if a default is not explicitly defined.
        field->default_value_enum_ = field->enum_type()->value(0);
      }
```

**File:** java/core/src/main/java/com/google/protobuf/MessageReflection.java (L1268-1279)
```java
        case ENUM:
          final int rawValue = input.readEnum();
          if (field.legacyEnumFieldTreatedAsClosed()) {
            value = field.getEnumType().findValueByNumber(rawValue);
            // If the number isn't recognized as a valid value for this enum,
            // add it to the unknown fields.
            if (value == null) {
              if (unknownFields != null) {
                unknownFields.mergeVarintField(fieldNumber, rawValue);
              }
              return true;
            }
```

**File:** src/google/protobuf/parse_context.h (L1464-1474)
```text
PROTOBUF_FUTURE_ADD_EARLY_NODISCARD
inline const char* ParseContext::ReadSizeAndPushLimitAndDepthInlined(
    const char* ptr, LimitToken* old_limit) {
  int size = ReadSize(&ptr);
  if (ABSL_PREDICT_FALSE(!ptr) || depth_ <= 0) {
    return nullptr;
  }
  *old_limit = PushLimit(ptr, size);
  --depth_;
  return ptr;
}
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitivesMessages.cs (L94-100)
```csharp
        public static void ReadMessage(ref ParseContext ctx, IMessage message)
        {
            int length = ParsingPrimitives.ParseLength(ref ctx.buffer, ref ctx.state);
            if (ctx.state.recursionDepth >= ctx.state.recursionLimit)
            {
                throw InvalidProtocolBufferException.RecursionLimitExceeded();
            }
```

**File:** objectivec/GPBCodedInputStream.m (L53-57)
```text
GPB_INLINE void CheckRecursionLimit(GPBCodedInputStreamState *state) {
  if (state->recursionDepth >= kDefaultRecursionLimit) {
    GPBRaiseStreamError(GPBCodedInputStreamErrorRecursionDepthExceeded, nil);
  }
}
```
