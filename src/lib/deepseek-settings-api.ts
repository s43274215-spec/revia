import { apiRequest } from "./api-base";

export type DeepSeekStatus = { configured: boolean; masked_hint?: string | null };
export type DeepSeekActionResult = { configured: boolean; masked_hint?: string | null; message: string };
export type DeepSeekConnectionResult = { success: boolean; message: string };

type TransportKey = { algorithm: string; public_key: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return apiRequest<T>(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

function pemToArrayBuffer(pem: string): ArrayBuffer {
  const encoded = pem.replace(/-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----|\s/g, "");
  const binary = window.atob(encoded);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return bytes.buffer;
}

async function encryptAPIKey(apiKey: string): Promise<string> {
  const transport = await request<TransportKey>("/settings/deepseek/encryption-key");
  const publicKey = await window.crypto.subtle.importKey(
    "spki",
    pemToArrayBuffer(transport.public_key),
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"],
  );
  const encrypted = await window.crypto.subtle.encrypt(
    { name: "RSA-OAEP" },
    publicKey,
    new TextEncoder().encode(apiKey),
  );
  return window.btoa(String.fromCharCode(...new Uint8Array(encrypted)));
}

export function getDeepSeekStatus(): Promise<DeepSeekStatus> {
  return request<DeepSeekStatus>("/settings/deepseek");
}

export async function saveDeepSeekAPIKey(apiKey: string): Promise<DeepSeekActionResult> {
  const encryptedAPIKey = await encryptAPIKey(apiKey);
  return request<DeepSeekActionResult>("/settings/deepseek", {
    method: "PUT",
    body: JSON.stringify({ encrypted_api_key: encryptedAPIKey }),
  });
}

export async function testDeepSeekConnection(apiKey?: string): Promise<DeepSeekConnectionResult> {
  const encryptedAPIKey = apiKey ? await encryptAPIKey(apiKey) : undefined;
  return request<DeepSeekConnectionResult>("/settings/deepseek/test", {
    method: "POST",
    body: JSON.stringify({ encrypted_api_key: encryptedAPIKey }),
  });
}
