import fs from "node:fs";
import path from "node:path";

const dir = path.resolve(__dirname, "../../contract/fixtures");
export const fixtureJson = <T = any>(name: string): T => JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), "utf8"));
export const fixtureText = (name: string) => fs.readFileSync(path.join(dir, name), "utf8");
export const conversation = () => fixtureJson("conversation");
/** First card of a type found in the conversation fixture. */
export function cardOf(type: string) {
  for (const m of conversation().messages) for (const c of m.cards) if (c.type === type) return c;
  throw new Error(`no ${type} card in fixture`);
}
