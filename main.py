#pylint: disable = line-too-long, missing-module-docstring, missing-class-docstring, missing-function-docstring, invalid-name, too-many-lines, too-many-branches, no-name-in-module

import os
import json
import copy
import urllib.parse
import asyncio
import logging
import aiohttp
import yaml
import requests

class MatrixGenerator:
    owner = "conan-io"
    repo = "conan-center-index"

    def __init__(self, token=None, user=None, pw=None):
        self.session = requests.session()
        self.session.headers = {}
        if token:
            self.session.headers["Authorization"] = f"token {token}"

        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "request"

        self.session.auth = None
        if user and pw:
            self.session.auth = requests.auth.HTTPBasicAuth(user, pw)

        self.prs = {}

        page = 1
        while True:
            r = self.session.request("GET", f"https://api.github.com/repos/{self.owner}/{self.repo}/pulls", params={
                "state": "open",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
                "page": str(page)
            })
            r.raise_for_status()
            results = r.json()
            for p in results:
                self.prs[int(p["number"])] = p
            page += 1
            if not results:
                break

        async def _populate_diffs():
            async with aiohttp.ClientSession() as session:
                async def _populate_diff(pr):
                    async with session.get(self.prs[pr]["diff_url"]) as r:
                        r.raise_for_status()
                        self.prs[pr]["libs"] = set()
                        try:
                            diff = await r.text()
                        except UnicodeDecodeError:
                            logging.error("error when decoding diff at %s", self.prs[pr]["diff_url"])
                            return
                        for line in diff.split("\n"):
                            if line.startswith("+++ b/recipes/") or line.startswith("--- a/recipes/"):
                                parts = line.split("/")
                                if len(parts) >= 5:
                                    self.prs[pr]["libs"].add(f"{parts[2]}/{parts[3]}")
                await asyncio.gather(*[asyncio.create_task(_populate_diff(pr)) for pr in self.prs])

        loop = asyncio.get_event_loop()
        loop.run_until_complete(_populate_diffs())

    async def generate_matrix(self):
        res = []

        async with aiohttp.ClientSession() as session:

            async def _add_package(package, repo, ref, pr = "0"):
                refs = package.split("/")
                package = refs[0]
                modified_folder = refs[1] if len(refs) >= 2 else ""
                async with session.get(f"https://raw.githubusercontent.com/{repo}/{ref}/recipes/{package}/config.yml") as r:
                    if r.status  == 404:
                        folder = "system"
                        if modified_folder and modified_folder != folder:
                            return
                        async with session.get(f"https://raw.githubusercontent.com/{repo}/{ref}/recipes/{package}/{folder}/conanfile.py") as r:
                            if r.status  == 404:
                                logging.warning("no system folder found for package %s in pr %s %s", package, pr, r.url)
                                return
                            r.raise_for_status()
                    else:
                        r.raise_for_status()
                        try:
                            config = yaml.safe_load(await r.text())
                        except yaml.YAMLError as exc:
                            logging.warning("Error in configuration file:%s, %s, %s, %s, %s", package, repo, ref, pr, exc)
                            return
                        if "system" not in config["versions"]:
                            return
                        folder = config["versions"]["system"]["folder"]
                        if modified_folder and modified_folder != folder:
                            return
                res.append({
                        'package': package,
                        'repo': repo,
                        'ref': ref,
                        'folder': folder,
                        'pr': pr,
                    })
            tasks = []
            for package in  os.listdir("CCI/recipes"):
                tasks.append(asyncio.create_task(_add_package(package, f'{self.owner}/{self.repo}', 'master')))

            for pr in self.prs.values():
                pr_number = str(pr["number"])
                for package in pr['libs']:
                    if not pr["head"]["repo"]:
                        logging.warning("no repo detected for pr #%s", pr_number)
                        continue
                    tasks.append(asyncio.create_task(_add_package(package, pr["head"]["repo"]["full_name"], urllib.parse.quote_plus(pr["head"]["ref"]), pr_number)))

            await asyncio.gather(*tasks)

        job_id = 0
        for p in res:
            p["job_id"] = job_id
            job_id += 1

        linux = []
        for p in res:
            for distro in [
                            "opensuse/tumbleweed",
                            "opensuse/leap:15.2",
                            "debian:11",
                            "debian:10",
                            "ubuntu:kinetic",
                            "ubuntu:jammy",
                            "ubuntu:focal",
                            "ubuntu:bionic",
                            "almalinux:8.5",
                            "archlinux",
                            "fedora:36",
                            "fedora:35",
                            "fedora:34",
                            "fedora:33",
                            "quay.io/centos/centos:stream8",
                            # "quay.io/centos/centos:stream9", # Error: Unable to find a match: libXvMC-devel
            ]:
                config = copy.deepcopy(p)
                config['distro'] = distro
                linux.append(config)


        with open("matrixLinux.yml", "w", encoding="latin_1") as f:
            json.dump({"include": linux}, f)


        with open("matrixBSD.yml", "w", encoding="latin_1") as f:
            json.dump({"include": res}, f)



def main():
    d = MatrixGenerator(token=os.getenv("GH_TOKEN"))
    loop = asyncio.get_event_loop()
    loop.run_until_complete(d.generate_matrix())


if __name__ == "__main__":
    # execute only if run as a script
    main()
