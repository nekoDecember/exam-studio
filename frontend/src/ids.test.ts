import { afterEach, expect, it, vi } from "vitest";
import { randomId } from "./ids";
import { newAttempt } from "./store";
afterEach(() => vi.unstubAllGlobals());
it("starts practice and creates unique UUIDs on LAN HTTP without randomUUID", () => {
  const getRandomValues = crypto.getRandomValues.bind(crypto);
  vi.stubGlobal("crypto", { getRandomValues });
  const ids = Array.from({ length: 100 }, randomId);
  expect(new Set(ids).size).toBe(100);
  expect(ids.every((id) => /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id))).toBe(true);
  expect(newAttempt([], "LAN演習").id).toMatch(/^[0-9a-f-]{36}$/);
});
