import { graphql } from "~/helpers/backend/graphql"

export const listItems = () => graphql("items")
