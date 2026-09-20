import { describe, it, expect, vi } from "vitest";
import { sendOnCtrlEnter } from "./ui";

/** A key event as Mantine's onKeyDown hands it over. */
function key(k: string, mods: { ctrlKey?: boolean; metaKey?: boolean } = {}) {
  return { key: k, ctrlKey: false, metaKey: false, preventDefault: vi.fn(), ...mods };
}

describe("sendOnCtrlEnter", () => {
  it("sends on Ctrl+Enter", () => {
    const send = vi.fn();
    sendOnCtrlEnter(send)(key("Enter", { ctrlKey: true }));
    expect(send).toHaveBeenCalledOnce();
  });

  it("sends on Cmd+Enter, for the same reason on a Mac", () => {
    const send = vi.fn();
    sendOnCtrlEnter(send)(key("Enter", { metaKey: true }));
    expect(send).toHaveBeenCalledOnce();
  });

  it("does NOT send on a bare Enter — that is a newline", () => {
    // These boxes hold notes and replies people write in more than one line. A bare
    // Enter posting half a thought is the bug, not the feature.
    const send = vi.fn();
    sendOnCtrlEnter(send)(key("Enter"));
    expect(send).not.toHaveBeenCalled();
  });

  it("ignores every other key, modified or not", () => {
    const send = vi.fn();
    const handler = sendOnCtrlEnter(send);
    handler(key("a", { ctrlKey: true }));
    handler(key("Escape"));
    handler(key(" ", { metaKey: true }));
    expect(send).not.toHaveBeenCalled();
  });

  it("stops the keystroke also inserting a newline", () => {
    const e = key("Enter", { ctrlKey: true });
    sendOnCtrlEnter(vi.fn())(e);
    expect(e.preventDefault).toHaveBeenCalled();
  });
});
