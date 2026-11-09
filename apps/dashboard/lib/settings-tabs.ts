/** The Settings page's tabs. Shared by the server page and the tab bar. */
export type SettingsTab = "workspace" | "plan" | "keys" | "access" | "security";

/** `short` is the label below sm, so the row stays short; past the screen
 *  edge it scrolls on its own (Tabs), never the page. */
export const SETTINGS_TABS: readonly { id: SettingsTab; label: string; short?: string }[] = [
  { id: "workspace", label: "Workspace" },
  { id: "plan", label: "Plan & usage", short: "Plan" },
  { id: "keys", label: "API keys", short: "Keys" },
  { id: "access", label: "Access log", short: "Access" },
  { id: "security", label: "Security" },
];

export function settingsTabOf(value: string | null | undefined): SettingsTab {
  return SETTINGS_TABS.find((t) => t.id === value)?.id ?? "workspace";
}
