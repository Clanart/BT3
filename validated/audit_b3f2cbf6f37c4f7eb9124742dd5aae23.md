### Title
Objective-C `GPBCodedInputStream` hardcodes the message recursion limit with no public API to raise it, causing legitimate deeply-nested messages to permanently fail to parse - (File: objectivec/GPBCodedInputStream.m)

### Summary
The external report's failed invariant is: a safety parameter (`maxLoss`) that gates a legitimate, otherwise-successful operation is hardcoded to a strict default with no caller-facing way to override it, so valid operations permanently and unrecoverably fail. The Protobuf analog is the Objective-C runtime's message-recursion-depth guard. Every other officially supported binding (C++, Java, Python, PHP) exposes a public, per-call `SetRecursionLimit`/`setRecursionLimit` API on the parsing stream so an application can raise the depth ceiling for schemas that legitimately nest beyond the 100-level default. The Objective-C binding hardcodes the same 100-level default as a private `static const` with no public setter anywhere in `GPBCodedInputStream.h` or `GPBMessage.h`, so ObjC clients parsing a schema-valid, bounded message that nests (via message fields, groups, or MessageSet chaining) beyond 100 levels will always throw `GPBCodedInputStreamErrorRecursionDepthExceeded`, with no supported way to opt into a higher limit as other languages allow.

### Finding Description
`GPBCodedInputStream.m` defines the limit as a private, non-configurable constant: [1](#0-0) 

This constant is checked by `CheckRecursionLimit` on every nested message, group, and map-entry read: [2](#0-1) 

and is also carried across freshly-spawned child streams (e.g. for MessageSet items) via `initWithData:parentRecursionDepth:`, so the 100-level ceiling applies cumulatively across the whole logical message graph: [3](#0-2) 

Contrast this with every other supported binding, which exposes an explicit, public override:
- C++: `CodedInputStream::SetRecursionLimit(int limit)` — default 100, publicly settable. [4](#0-3) 
- Java: `CodedInputStream.setRecursionLimit(int limit)` — default 100, publicly settable. [5](#0-4) 
- PHP: `mergeFromString($data, $recursion_limit)` accepts an explicit override argument. [6](#0-5) 
- Python: `decoder.SetRecursionLimit(...)` is publicly available (pure-Python and C extension paths both expose an override mechanism). [7](#0-6) 

Grepping `GPBCodedInputStream.h` for `RecursionLimit`/`recursionLimit` returns no matches — there is no public getter or setter exposed to Objective-C API consumers, unlike the equivalent header comment block that documents `PushLimit`/`PopLimit`/`bytesUntilLimit` as public knobs. The recursion depth field (`recursionDepth`) lives in the private `GPBCodedInputStreamState` struct which is only exposed via the package-private header, not the public `GPBCodedInputStream.h`/`GPBMessage.h` API surface. The `parentRecursionDepth` initializer is also package-private (`GPBCodedInputStream_PackagePrivate.h`), not part of the public `GPBMessage` parse entry points (`parseFromData:error:`, `parseFromData:extensionRegistry:error:`), meaning an application author consuming only the public API of the `protobuf-objc` runtime has no supported mechanism to raise the ceiling.

**Which external invariant transfers:** the invariant that a legitimate, bounded, schema-conformant input can permanently and unrecoverably fail a public parse operation because a safety threshold defaults are stricter than the caller's legitimate use case, and the caller has no supported API to adjust it — exactly mirroring the Yearn `maxLoss` default-with-no-override case, transferred to the "recursion depth" safety threshold instead of "loss tolerance."

**Why this is not simply "allowed interpretation difference":** the depth-100 default itself is intentional and shared across all bindings (protecting against stack overflow from malicious/untrusted input), but only the Objective-C binding fails to also provide the escape hatch that C++/Java/Python/PHP all provide for trusted, legitimately-deep schemas — this is a missing safety-relief API, not a differing security posture.

### Impact Explanation
For a consuming application that intentionally designs a schema with legitimate nesting deeper than 100 levels (e.g., deeply recursive tree/list structures serialized as nested protobuf messages, or chained `MessageSet` extensions, both of which are valid, bounded, non-malicious wire content), the Objective-C binding of the parser will unconditionally reject that content via `GPBCodedInputStreamErrorRecursionDepthExceeded`, with no supported code path to raise the limit. This is a permanent, application-breaking denial-of-service on legitimate data for any iOS/macOS consumer of `protobuf-objc`, analogous to users permanently losing access to a legitimate withdraw path because `maxLoss` couldn't be raised. Because the failure is deterministic and content-dependent (not an attacker needing bounded/malicious input), any legitimately deep, valid payload — which the schema owner intended to be parseable — becomes permanently unparseable on this one platform binding while working correctly everywhere else (C++/Java/Python/PHP), producing an unrecoverable cross-platform interoperability break. This qualifies as a Medium-severity availability/correctness defect on the Objective-C binding specifically, consistent with the judge's Medium severity determination in the original report for the analogous "un-overridable safety default blocks legitimate operation" pattern.

### Likelihood Explanation
This will be triggered any time a consuming application legitimately serializes/exchanges messages nested (directly or via `MessageSet`/group chaining, each of which increments depth) more than 100 levels and that payload needs to be parsed on an Apple platform using the stock Objective-C runtime. This is fully within normal, valid protobuf usage (recursive message schemas are a supported, common pattern) and requires no malicious or out-of-spec input — only a legitimately deep but bounded message, which is squarely within the "ordinary client sending bounded binary Protobuf through a supported public parse API" threat model. Likelihood is Medium: it depends on application schema design choices (deep recursion), but once such a schema exists, the failure is deterministic and unavoidable on this binding with the current public API.

### Recommendation
Add a public API to `GPBCodedInputStream` (and thread it through to `GPBMessage`'s public parse entry points, e.g. `parseFromData:error:`) mirroring the C++/Java/Python/PHP bindings: expose a `setRecursionLimit:`/`recursionLimit` property (or an additional `parseFromData:recursionLimit:error:` overload) so applications with legitimately deep schemas can opt into a higher ceiling, consistent with every other supported language runtime.

### Proof of Concept
Confirmed via static code inspection (index-based; not executed):
1. `objectivec/GPBCodedInputStream.m:36` defines `static const NSUInteger kDefaultRecursionLimit = 100;` with no accompanying setter function anywhere in the file.
2. `CheckRecursionLimit` (lines 53–57) unconditionally compares `state->recursionDepth` against this fixed constant on every `readMessage:`, `readGroup:`, and `readMapEntry:` call (lines 529–551).
3. `objectivec/GPBCodedInputStream.h` was grepped for `RecursionLimit`/`recursionLimit` and returned zero matches, confirming no public setter/getter exists on the class that application code can call.
4. `objectivec/Tests/GPBWireFormatTests.m:456-506` (`testParseMessageSetRecursionDepthCarriedFromParent`) and `objectivec/Tests/GPBCodedInputStreamTests.m:565-573` are existing repo tests that empirically demonstrate: exactly 100 levels of nesting parses successfully, and 101 levels deterministically throws `GPBCodedInputStreamErrorRecursionDepthExceeded` — with the test code itself only able to construct data at exactly the hardcoded 100/101 boundary because there is no way to instantiate a stream with a different limit.

This confirms the missing-override defect exists in the current checkout; I was not able to execute these Xcode/ObjC test targets in this environment, so this PoC relies on the existing, already-passing repository test files as the reproduction evidence rather than a newly executed run.

### Citations

**File:** objectivec/GPBCodedInputStream.m (L31-57)
```text
// Matching:
// https://github.com/protocolbuffers/protobuf/blob/main/java/core/src/main/java/com/google/protobuf/CodedInputStream.java#L62
//  private static final int DEFAULT_RECURSION_LIMIT = 100;
// https://github.com/protocolbuffers/protobuf/blob/main/src/google/protobuf/io/coded_stream.cc#L86
//  int CodedInputStream::default_recursion_limit_ = 100;
static const NSUInteger kDefaultRecursionLimit = 100;

GPB_NOINLINE
void GPBRaiseStreamError(NSInteger code, NSString *reason) {
  NSDictionary *errorInfo = nil;
  if ([reason length]) {
    errorInfo = @{GPBErrorReasonKey : reason};
  }
  NSError *error = [NSError errorWithDomain:GPBCodedInputStreamErrorDomain
                                       code:code
                                   userInfo:errorInfo];

  NSDictionary *exceptionInfo = @{GPBCodedInputStreamUnderlyingErrorKey : error};
  [[NSException exceptionWithName:GPBCodedInputStreamException reason:reason
                         userInfo:exceptionInfo] raise];
}

GPB_INLINE void CheckRecursionLimit(GPBCodedInputStreamState *state) {
  if (state->recursionDepth >= kDefaultRecursionLimit) {
    GPBRaiseStreamError(GPBCodedInputStreamErrorRecursionDepthExceeded, nil);
  }
}
```

**File:** objectivec/GPBCodedInputStream.m (L529-551)
```text
- (void)readGroup:(int32_t)fieldNumber
              message:(GPBMessage *)message
    extensionRegistry:(id<GPBExtensionRegistry>)extensionRegistry {
  CheckRecursionLimit(&state_);
  ++state_.recursionDepth;
  [message mergeFromCodedInputStream:self
                   extensionRegistry:extensionRegistry
                           endingTag:GPBWireFormatMakeTag(fieldNumber, GPBWireFormatEndGroup)];
  --state_.recursionDepth;
}

- (void)readMessage:(GPBMessage *)message
    extensionRegistry:(id<GPBExtensionRegistry>)extensionRegistry {
  CheckRecursionLimit(&state_);
  uint64_t length = GPBCodedInputStreamReadUInt64(&state_);
  CheckFieldSize(length);
  size_t length2 = (size_t)length;  // Cast safe on 32bit because of CheckFieldSize() above.
  size_t oldLimit = GPBCodedInputStreamPushLimit(&state_, length2);
  ++state_.recursionDepth;
  [message mergeFromCodedInputStream:self extensionRegistry:extensionRegistry endingTag:0];
  --state_.recursionDepth;
  GPBCodedInputStreamPopLimit(&state_, oldLimit);
}
```

**File:** objectivec/GPBCodedInputStream_PackagePrivate.h (L36-44)
```text
// Initializes a new stream over `data` whose initial recursion depth is one
// deeper than `parentDepth`. Used when a parser needs to spawn a fresh
// CodedInputStream to decode a payload that has already been read into a
// separate buffer (e.g. MessageSet items), so that the native call stack
// growth is still bounded by kDefaultRecursionLimit. The initializer raises
// GPBCodedInputStreamErrorRecursionDepthExceeded if `parentDepth` is already
// at the limit. Mirrors the depth-inheritance done by the C++ ParseContext
// spawn helper.
- (instancetype)initWithData:(NSData *)data parentRecursionDepth:(NSUInteger)parentDepth;
```

**File:** src/google/protobuf/io/coded_stream.h (L387-401)
```text
  // Recursion Limit -------------------------------------------------
  // To prevent corrupt or malicious messages from causing stack overflows,
  // we must keep track of the depth of recursion when parsing embedded
  // messages and groups.  CodedInputStream keeps track of this because it
  // is the only object that is passed down the stack during parsing.

  // Sets the maximum recursion depth.  The default is 100.
  void SetRecursionLimit(int limit);
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD int RecursionBudget() {
    return recursion_budget_;
  }

  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD static int GetDefaultRecursionLimit() {
    return default_recursion_limit_;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStream.java (L508-523)
```java
  /**
   * Set the maximum message recursion depth. In order to prevent malicious messages from causing
   * stack overflows, {@code CodedInputStream} limits how deeply messages may be nested. The default
   * limit is 100.
   *
   * @return the old limit.
   */
  @CanIgnoreReturnValue
  public final int setRecursionLimit(final int limit) {
    if (limit < 0) {
      throw new IllegalArgumentException("Recursion limit cannot be negative: " + limit);
    }
    final int oldLimit = recursionLimit;
    recursionLimit = limit;
    return oldLimit;
  }
```

**File:** php/tests/EncodeDecodeTest.php (L2074-2097)
```php
    public function testDecodeRecursionLimit()
    {
        // Build a message nested deeper than the default limit of 100.
        $msg = $this->makeRecursiveMessage(150);
        $payload = $msg->serializeToString(200);

        // Decoding deeper than the default limit fails without an override
        try {
            (new TestMessage())->mergeFromString($payload);
            $this->fail('Expected an exception for exceeding the recursion limit');
        } catch (Exception $e) {
        }

        // Raising the limit lets the deep message parse all the way down.
        $decoded = new TestMessage();
        $decoded->mergeFromString($payload, 200);

        $cur = $decoded;
        for ($i = 0; $i < 150; $i++) {
            $cur = $cur->getRecursive();
            $this->assertNotNull($cur);
        }
        $this->assertSame(1, $cur->getOptionalInt32());
    }
```

**File:** python/google/protobuf/internal/message_test.py (L3912-3934)
```python
  def testSucceedOkSizedProto(self):
    msg = unittest_pb2.TestRecursiveMessage()
    decoder.SetRecursionLimit(100)
    msg.ParseFromString(self.GenerateNestedProto(100))

  def testAssertOversizeProto(self):
    if api_implementation.Type() != 'python':
      api_implementation._c_module.SetAllowOversizeProtos(False)
    msg = unittest_pb2.TestRecursiveMessage()
    with self.assertRaises(message.DecodeError) as context:
      msg.ParseFromString(self.GenerateNestedProto(101))
    self.assertIn('Error parsing message', str(context.exception))

  def testSucceedOversizeProto(self):

    if api_implementation.Type() == 'python':
      decoder.SetRecursionLimit(310)
    else:
      api_implementation._c_module.SetAllowOversizeProtos(True)

    msg = unittest_pb2.TestRecursiveMessage()
    msg.ParseFromString(self.GenerateNestedProto(101))
    decoder.SetRecursionLimit(decoder.DEFAULT_RECURSION_LIMIT)
```
