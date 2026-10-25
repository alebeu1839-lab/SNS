/**
 * Safe, deterministic task-specific deliverable.
 * Never invent affiliate offer terms, product claims, URLs, or performance metrics.
 * This is a reviewable draft, not an executed marketing campaign.
 */
export function buildReviewDraft({ goalId, title, taskIdentity, date }) {
  for (const [key, value] of Object.entries({ goalId, title, taskIdentity, date })) {
    if (typeof value !== 'string' || !value.trim()) throw new Error(`Missing ${key}`);
  }
  const cleanTitle = title.replace(/[\r\n]+/g, ' ').trim().slice(0, 180);
  return {
    type: 'human_review_draft',
    business_task_completed: false,
    published: false,
    approval_required: true,
    goal_id: goalId,
    task_identity: taskIdentity,
    date,
    title: cleanTitle,
    deliverable: [
      `対象ゴール: ${cleanTitle}`,
      '作業案: 対象ゴールの目的・対象顧客・提供価値を確認し、検証可能な投稿草案を作る。',
      '投稿草案（未公開）:',
      `「${cleanTitle}」について、知っておきたいポイントを整理しています。詳しい内容は確認でき次第お伝えします。`,
      '要確認: 実在する案件名、広告主のSNS掲載可否、成果条件、表現規制、承認済みリンク。',
      '次の操作: 根拠を確認してから本人承認を求める。承認までは外部公開しない。',
    ].join('\n'),
  };
}
