import { BLOG_POSTS } from './data';
import BlogListClient from './BlogListClient';
import { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Blog | AgentWatch',
  description:
    'Latest industry updates, open-source AI breakthroughs, and in-depth explorations of agentic workflows.',
};

export default function BlogPage() {
  // Strip out full article content to save client bundle size
  const previewPosts = BLOG_POSTS.map(({ content, ...post }) => post);

  return <BlogListClient posts={previewPosts} />;
}
