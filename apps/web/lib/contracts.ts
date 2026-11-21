import { z } from "zod";
import type { components } from "../../../packages/contracts/src/api";

/** Request-body schemas enforced at the BFF boundary (mirrors the FastAPI models; the API validates again). */
export const WorkspaceCreate = z.object({ name: z.string().trim().min(1).max(80), slug: z.string().regex(/^[a-z0-9][a-z0-9-]{1,38}$/).optional() }).strict();
export const ProjectCreate = z.object({ name: z.string().trim().min(1).max(80) }).strict();
export const RuntimeCreate = z.object({ name: z.string().trim().min(1).max(80), is_mock: z.boolean().default(false), expires_days: z.number().int().min(1).max(90).default(30) }).strict();
export const ConversationCreate = z.object({ title: z.string().max(120).nullish(), runtime_id: z.string().uuid().nullish() }).strict();
export const SubmitJob = z
  .object({ runtime_id: z.string().uuid(), content: z.string().min(1).max(16000), max_tokens: z.number().int().min(1).max(512).default(256), temperature: z.number().min(0).max(2).default(0.2) })
  .strict();

export type SubmitJobBody = components["schemas"]["SubmitJob"];
