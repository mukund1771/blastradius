export interface RestRequest {
  url: string
}

export const parse = (raw: string): RestRequest => ({ url: raw })
