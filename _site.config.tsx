export function Header({basePath}: {basePath: string}) {
    return (
        <div className="flex w-full flex-col gap-4 sm:flex-row sm:items-baseline sm:justify-between">
            <a href={`${basePath}/`} className="site-wordmark">Home</a>
            <nav className="flex flex-wrap gap-x-4 gap-y-2 font-mono text-sm tracking-wide">
                <a href={`${basePath}/all.html`}>All articles</a>
                <a href="https://github.com/Monster0506/readlog">GitHub</a>
                <a href={`${basePath}/feed-links.xml`}>Links RSS</a>
                <a href={`${basePath}/feed-roundups.xml`}>Roundups RSS</a>
            </nav>
        </div>
    );
}
