export function Header({basePath}: {basePath: string}) {
    return (
        <>
            <a href={`${basePath}/`} className="site-wordmark">Home</a>
            <nav className="flex gap-4 font-mono text-sm tracking-wide">
                <a href="https://github.com/Monster0506/readlog">GitHub</a>
                <a href={`${basePath}/feed-links.xml`}>Links RSS</a>
                <a href={`${basePath}/feed-roundups.xml`}>Roundups RSS</a>
            </nav>
        </>
    );
}
