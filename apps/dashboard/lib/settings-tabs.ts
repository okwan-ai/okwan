/** The Settings page's tabs. Shared by the server page and the tab bar. */
export const SETTINGS_TABS = [
  { id: "workspace", label: "Workspace" },
  { id: "plan", label: "Plan & usage" },
  { id: "keys", label: "API keys" },
  { id: "security", label: "Security" },
] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number]["id"];

export function settingsTabOf(value: string | null | undefined): SettingsTab {
  return SETTINGS_TABS.find((t) => t.id === value)?.id ?? "workspace";
}
