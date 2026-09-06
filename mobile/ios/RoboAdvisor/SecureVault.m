#import <React/RCTBridgeModule.h>

// Bridge declaration for the Swift implementation in SecureVault.swift.
// Swift classes are exposed to React Native through an Objective-C interface;
// this file is the whole of it.
@interface RCT_EXTERN_MODULE (SecureVault, NSObject)

RCT_EXTERN_METHOD(setToken
                  : (NSString *)token account
                  : (NSString *)account resolver
                  : (RCTPromiseResolveBlock)resolve rejecter
                  : (RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(getToken
                  : (NSString *)account resolver
                  : (RCTPromiseResolveBlock)resolve rejecter
                  : (RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(deleteToken
                  : (NSString *)account resolver
                  : (RCTPromiseResolveBlock)resolve rejecter
                  : (RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(biometryType
                  : (RCTPromiseResolveBlock)resolve rejecter
                  : (RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(authenticate
                  : (NSString *)reason resolver
                  : (RCTPromiseResolveBlock)resolve rejecter
                  : (RCTPromiseRejectBlock)reject)

@end
