// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import actionsData from './actions.json'
import topicsData from './topics.json'
import examplePromptsData from './examplePrompts.json'

export interface ActionItem {
  id: string
  label: string
}

export interface TopicItem {
  id: string
  label: string
}

export interface ExamplePrompt {
  id: string
  text: string
  action: string
  topic: string
}

export const actions: ActionItem[] = actionsData.actions
export const topics: TopicItem[] = topicsData.topics
export const examplePrompts: ExamplePrompt[] = examplePromptsData.prompts
