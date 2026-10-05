/** The Settings page's tabs. Shared by the server page and the tab bar. */
export type SettingsTab = "workspace" | "plan" | "keys" | "security";

/** `short` is the label below sm, so all four tabs fit in about 310px. */
export const SETTINGS_TABS: readonly { id: SettingsTab; label: string; short?: string }[] = [
  { id: "workspace", label: "Workspace" },
  { id: "plan", label: "Plan & usage", short: "Plan" },
  { id: "keys", label: "API keys", short: "Keys" },
  { id: "security", label: "Security" },
];

export function settingsTabOf(value: string | null | undefined): SettingsTab {
  return SETTINGS_TABS.find((t) => t.id === value)?.id ?? "workspace";
}
