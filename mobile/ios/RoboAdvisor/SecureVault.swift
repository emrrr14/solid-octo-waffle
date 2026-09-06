import Foundation
import LocalAuthentication
import Security

/// Keychain + Face ID for session tokens.
///
/// Why this is native code and not a JS library:
///
/// * `AsyncStorage` is a plaintext SQLite file inside the app container.  It is
///   fine for a "last selected tab"; it is not where a bearer token that can
///   move money belongs.  Only the Keychain gives hardware-backed storage that
///   survives reinstall policy, iCloud-backup exclusion and device-only
///   scoping - and it is reachable exclusively through Security.framework.
/// * `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` keeps the token off
///   backups and off any restored device.  This is the single most important
///   line in the file.
/// * Face ID lives in `LocalAuthentication`, which has no JS equivalent either.
///
/// Everything else in this app is TypeScript.  Native code is a cost; it earns
/// its place here and nowhere else so far.
@objc(SecureVault)
final class SecureVault: NSObject {

  private let service = "com.roboadvisor.session"

  /// The JS thread is not the UI thread; nothing here touches UIKit, so we let
  /// React Native keep this module off the main queue.
  @objc static func requiresMainQueueSetup() -> Bool { false }

  // MARK: - Keychain

  @objc(setToken:account:resolver:rejecter:)
  func setToken(
    _ token: String,
    account: String,
    resolve: RCTPromiseResolveBlock,
    reject: RCTPromiseRejectBlock
  ) {
    guard let data = token.data(using: .utf8) else {
      reject("encoding_failed", "Token is not valid UTF-8", nil)
      return
    }

    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]

    // Delete-then-add: SecItemUpdate cannot change the accessibility class, and
    // a token written under the wrong class is a silent backup leak.
    SecItemDelete(query as CFDictionary)

    var attributes = query
    attributes[kSecValueData as String] = data
    attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly

    let status = SecItemAdd(attributes as CFDictionary, nil)
    guard status == errSecSuccess else {
      reject("keychain_write_failed", "Keychain returned \(status)", nil)
      return
    }
    resolve(true)
  }

  @objc(getToken:resolver:rejecter:)
  func getToken(
    _ account: String,
    resolve: RCTPromiseResolveBlock,
    reject: RCTPromiseRejectBlock
  ) {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
      kSecReturnData as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]

    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)

    switch status {
    case errSecSuccess:
      guard let data = item as? Data, let token = String(data: data, encoding: .utf8) else {
        reject("keychain_decode_failed", "Stored token is unreadable", nil)
        return
      }
      resolve(token)
    case errSecItemNotFound:
      resolve(nil)  // "signed out" is a normal state, not an error
    default:
      reject("keychain_read_failed", "Keychain returned \(status)", nil)
    }
  }

  @objc(deleteToken:resolver:rejecter:)
  func deleteToken(
    _ account: String,
    resolve: RCTPromiseResolveBlock,
    reject: RCTPromiseRejectBlock
  ) {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      reject("keychain_delete_failed", "Keychain returned \(status)", nil)
      return
    }
    resolve(true)
  }

  // MARK: - Biometrics

  @objc(biometryType:rejecter:)
  func biometryType(resolve: @escaping RCTPromiseResolveBlock, reject: RCTPromiseRejectBlock) {
    let context = LAContext()
    var error: NSError?
    guard context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) else {
      resolve("none")
      return
    }
    switch context.biometryType {
    case .faceID: resolve("face")
    case .touchID: resolve("touch")
    default: resolve("none")
    }
  }

  /// Gate an action behind Face ID.  Falls back to the device passcode, so a
  /// user whose Face ID fails in bright sun is not locked out of their money.
  @objc(authenticate:resolver:rejecter:)
  func authenticate(
    _ reason: String,
    resolve: @escaping RCTPromiseResolveBlock,
    reject: @escaping RCTPromiseRejectBlock
  ) {
    let context = LAContext()
    context.localizedFallbackTitle = "Use passcode"

    context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, error in
      // evaluatePolicy calls back on a private queue; hop to main so the JS
      // bridge callback is invoked from a predictable thread.
      DispatchQueue.main.async {
        if success {
          resolve(true)
        } else if let laError = error as? LAError, laError.code == .userCancel {
          resolve(false)  // cancelling is a choice, not a failure
        } else {
          reject("auth_failed", error?.localizedDescription ?? "Authentication failed", error)
        }
      }
    }
  }
}
