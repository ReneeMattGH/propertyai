import { RECONSTRUCTION_URL, errorMessage } from './reconstruction'

export interface IntakeBuild {
  job_id: string
  style: string
  num_floors: number
  known_width_meters: number | null
  known_length_meters: number | null
  scale_verified: boolean
  started_at: string
}

export interface IntakeFile {
  file_id: string
  name: string
  category: string
  kind: string
  mime_type: string
  size_bytes: number
  stored: boolean
  stored_path: string | null
  sha256: string | null
  error: string | null
  url: string
}

export interface IntakeClient {
  id?: string
  email?: string
  firstName?: string
  lastName?: string
  name?: string
  phone?: string
}

export interface IntakeProject {
  id?: string
  name?: string
  developer?: string
  location?: string
  type?: string
  developmentStage?: string
  buildings?: number
  floors?: number
  units?: number
  unitTypes?: string[]
  interiorStyle?: string
  exteriorStyle?: string
  planWidthMeters?: number | string | null
  planLengthMeters?: number | string | null
  furnishingStyle?: string
  materials?: string
  landscape?: string
  lighting?: string
  referenceProjects?: string
}

export interface IntakeRequestSummary {
  request_id: string
  project_id: string
  received_at: string
  updated_at: string
  status: string
  project_name: string
  client: { email: string; name: string }
  company_name: string
  plan_count: number
  plans_ready: boolean
  file_count: number
  build: IntakeBuild | null
  issues: string[]
}

export interface IntakeRequestDetail extends Omit<IntakeRequestSummary, 'client'> {
  files: IntakeFile[]
  project: IntakeProject
  company: { id: string; name: string; website: string; details: string } | null
  client: IntakeClient
  submitted_at: string | null
}

export interface IntakeList {
  total: number
  counts: Record<string, number>
  requests: IntakeRequestSummary[]
}

export interface BuildOverrides {
  style?: string
  num_floors?: number
  known_width?: number | null
  known_length?: number | null
}

const intakePath = (suffix = ''): string => `${RECONSTRUCTION_URL}/intake/requests${suffix}`

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', ...init })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json() as Promise<T>
}

export function fetchIntakeRequests(signal?: AbortSignal): Promise<IntakeList> {
  return requestJson<IntakeList>(intakePath(), { signal })
}

export function fetchIntakeRequest(requestId: string, signal?: AbortSignal): Promise<IntakeRequestDetail> {
  return requestJson<IntakeRequestDetail>(intakePath(`/${encodeURIComponent(requestId)}`), { signal })
}

export function intakeFileUrl(file: IntakeFile): string {
  return `${RECONSTRUCTION_URL}${file.url}`
}

export function startIntakeBuild(
  requestId: string,
  overrides: BuildOverrides = {},
): Promise<{ request: IntakeRequestSummary; job: Record<string, unknown> }> {
  return requestJson(intakePath(`/${encodeURIComponent(requestId)}/build`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(overrides),
  })
}

export function ignoreIntakeRequest(requestId: string): Promise<{ request: IntakeRequestSummary }> {
  return requestJson(intakePath(`/${encodeURIComponent(requestId)}/ignore`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}

export function spanValue(value: number | string | null | undefined): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? String(parsed) : ''
}
