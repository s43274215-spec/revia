import { apiRequest } from "./api-base";

export type SiteSettings = {
  public_access_enabled: boolean;
  updated_at: string;
};

export function getSiteSettings(): Promise<SiteSettings> {
  return apiRequest<SiteSettings>("/settings/site");
}

export function updateSiteSettings(publicAccessEnabled: boolean): Promise<SiteSettings> {
  return apiRequest<SiteSettings>("/settings/site", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ public_access_enabled: publicAccessEnabled }),
  });
}
