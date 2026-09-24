[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L3082-3089)
```text
    if (ABSL_PREDICT_FALSE(ptr == nullptr)) {
      if (arena == nullptr) {
        // Parsing failed. Delete the node that we didn't insert.
        map.DeleteNode(node);
      }
      map.InsertOrReplaceNodes(arena, nodes_to_insert, node_count);
      PROTOBUF_MUSTTAIL return Error(PROTOBUF_TC_PARAM_NO_DATA_PASS);
    }
```

**File:** src/google/protobuf/arena.cc (L149-184)
```text
void ChunkList::Cleanup(const SerialArena& arena) {
  Chunk* c = head_;
  if (c == nullptr) return;
  GetDeallocator deallocator(arena.parent_.AllocPolicy());

  // Iterate backwards in order to destroy in the right order.
  CleanupNode* it = next_ - 1;
  while (true) {
    CleanupNode* first = c->First();
    // A prefetch distance of 8 here was chosen arbitrarily.
    constexpr int kPrefetchDistance = 8;
    CleanupNode* prefetch = it;
    // Prefetch the first kPrefetchDistance nodes.
    for (int i = 0; prefetch >= first && i < kPrefetchDistance;
         --prefetch, ++i) {
      prefetch->Prefetch();
    }
    // For the middle nodes, run destructor and prefetch the node
    // kPrefetchDistance after the current one.
    for (; prefetch >= first; --it, --prefetch) {
      it->Destroy();
      prefetch->Prefetch();
    }
    // Note: we could consider prefetching `next` chunk earlier.
    absl::PrefetchToLocalCacheNta(c->next);
    // Destroy the rest without prefetching.
    for (; it >= first; --it) {
      it->Destroy();
    }
    Chunk* next = c->next;
    deallocator({c, c->size});
    if (next == nullptr) return;
    c = next;
    it = c->Last();
  };
}
```

**File:** java/core/src/main/java/com/google/protobuf/AbstractParser.java (L44-63)
```java
  @SuppressWarnings("PatternMatchingInstanceof")
  protected final void wrapAndThrowParseException(Exception e, MessageLite.Builder builder)
      throws InvalidProtocolBufferException {
    if (e instanceof InvalidProtocolBufferException) {
      throw ((InvalidProtocolBufferException) e).setUnfinishedMessage(builder.buildPartial());
    }
    if (e instanceof UninitializedMessageException) {
      throw ((UninitializedMessageException) e)
          .asInvalidProtocolBufferException()
          .setUnfinishedMessage(builder.buildPartial());
    }
    if (e instanceof IOException) {
      throw new InvalidProtocolBufferException((IOException) e)
          .setUnfinishedMessage(builder.buildPartial());
    }
    if (e instanceof RuntimeException) {
      throw (RuntimeException) e;
    }
    throw new InvalidProtocolBufferException(e).setUnfinishedMessage(builder.buildPartial());
  }
```

**File:** java/lite/src/test/java/com/google/protobuf/LiteTest.java (L1827-1867)
```java
  public void testParseFromArray_manyNestedMessagesError() throws Exception {
    RecursiveMessage.Builder recursiveMessage =
        RecursiveMessage.newBuilder().setPayload(ByteString.copyFrom(new byte[1]));
    for (int i = 0; i < 20; i++) {
      recursiveMessage = RecursiveMessage.newBuilder().setRecurse(recursiveMessage.build());
    }
    byte[] result = recursiveMessage.build().toByteArray();
    result[
            result.length
                - CodedOutputStream.computeTagSize(RecursiveMessage.PAYLOAD_FIELD_NUMBER)
                - CodedOutputStream.computeLengthDelimitedFieldSize(1)] =
        0; // Set invalid tag
    try {
      RecursiveMessage.parseFrom(result);
      assertWithMessage("Result was: %s", Arrays.toString(result)).fail();
    } catch (InvalidProtocolBufferException expected) {
      boolean found = false;
      int exceptionCount = 0;
      for (Throwable exception = expected; exception != null; exception = exception.getCause()) {
        if (exception instanceof InvalidProtocolBufferException) {
          exceptionCount++;
        }
        for (StackTraceElement element : exception.getStackTrace()) {
          if (InvalidProtocolBufferException.class.getName().equals(element.getClassName())
              && "invalidTag".equals(element.getMethodName())) {
            found = true;
          } else if (Android.isOnAndroidDevice()
              && "decodeUnknownField".equals(element.getMethodName())) {
            // Android is missing the first element of the stack trace - b/181147885
            found = true;
          }
        }
      }
      if (!found) {
        throw new AssertionError("Lost cause of parsing error", expected);
      }
      if (exceptionCount > 1) {
        throw new AssertionError(exceptionCount + " nested parsing exceptions", expected);
      }
    }
  }
```

**File:** objectivec/GPBCodedInputStream.m (L38-57)
```text
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
