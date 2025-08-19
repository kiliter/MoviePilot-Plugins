from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Optional, List, Tuple, Dict, Any, Union

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.chain.media import MediaChain
from app.core.config import settings
from app.core.metainfo import MetaInfo,MetaInfoPath
from app.db.transferhistory_oper import TransferHistoryOper
from app.helper.nfo import NfoReader
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import MediaType
from app.utils.system import SystemUtils
from app.chain.storage import StorageChain
from app.core.meta import MetaBase
from app.core.context import Context, MediaInfo
from app.utils.string import StringUtils
from app.utils.http import RequestUtils

class LibraryScraperOwn(_PluginBase):
    # 插件名称
    plugin_name = "媒体库刮削改"
    # 插件描述
    plugin_desc = "定时对媒体库进行刮削，补齐缺失元数据和图片。"
    # 插件图标
    plugin_icon = "scraperown.png"
    # 插件版本
    plugin_version = "2.1.1"
    # 插件作者
    plugin_author = "kiliter"
    # 作者主页
    author_url = "https://github.com/kiliter"
    # 插件配置项ID前缀
    plugin_config_prefix = "libraryscraperown_"
    # 加载顺序
    plugin_order = 7
    # 可使用的用户级别
    user_level = 1

    # 私有属性
    _scheduler = None
    _scraper = None
    # 限速开关
    _enabled = False
    _onlyonce = False
    _pre_day = 7
    _cron = None
    _mode = ""
    _scraper_paths = ""
    _exclude_paths = ""
    # 退出事件
    _event = Event()

    def init_plugin(self, config: dict = None):

        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._mode = config.get("mode") or ""
            self._scraper_paths = config.get("scraper_paths") or ""
            self._exclude_paths = config.get("exclude_paths") or ""
            self._pre_day = config.get("pre_day") or 7
            self.storagechain = StorageChain()

        # 停止现有任务
        self.stop_service()

        # 启动定时任务 & 立即运行一次
        if self._enabled or self._onlyonce:

            if self._onlyonce:
                logger.info(f"媒体库刮削服务，立即运行一次")
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.add_job(func=self.__libraryscraper, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                        name="媒体库刮削")
                # 关闭一次性开关
                self._onlyonce = False
                self.update_config({
                    "onlyonce": False,
                    "enabled": self._enabled,
                    "cron": self._cron,
                    "mode": self._mode,
                    "scraper_paths": self._scraper_paths,
                    "exclude_paths": self._exclude_paths
                })
                if self._scheduler.get_jobs():
                    # 启动服务
                    self._scheduler.print_jobs()
                    self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            return [{
                "id": "LibraryScraperOwn",
                "name": "媒体库刮削",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__libraryscraper,
                "kwargs": {}
            }]
        elif self._enabled:
            return [{
                "id": "LibraryScraperOwn",
                "name": "媒体库刮削",
                "trigger": CronTrigger.from_crontab("0 0 */7 * *"),
                "func": self.__libraryscraper,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VTextField',
                                'props': {
                                    'model': 'pre_day',
                                    'label': '近几天',
                                    'placeholder': '7',
                                }
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'mode',
                                            'label': '覆盖模式',
                                            'items': [
                                                {'title': '不覆盖已有元数据', 'value': ''},
                                                {'title': '覆盖所有元数据和图片', 'value': 'force_all'},
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式，留空自动'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'scraper_paths',
                                            'label': '削刮路径',
                                            'rows': 5,
                                            'placeholder': '每一行一个目录'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'exclude_paths',
                                            'label': '排除路径',
                                            'rows': 2,
                                            'placeholder': '每一行一个目录'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '刮削路径后拼接#电视剧/电影，强制指定该媒体路径媒体类型。'
                                                    '不加默认根据文件名自动识别媒体类型。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "cron": "0 0 */7 * *",
            "mode": "",
            "scraper_paths": "",
            "err_hosts": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def __libraryscraper(self):
        """
        开始刮削媒体库
        """
        if not self._scraper_paths:
            return
        # 排除目录
        exclude_paths = self._exclude_paths.split("\n")
        # 已选择的目录
        paths = self._scraper_paths.split("\n")
        # 需要适削的媒体文件夹
        scraper_paths = []
        for path in paths:
            if not path:
                continue
            # 强制指定该路径媒体类型
            mtype = None
            if str(path).count("#") == 1:
                mtype = next(
                    (mediaType for mediaType in MediaType.__members__.values() if
                     mediaType.value == str(str(path).split("#")[1])),
                    None)
                path = str(path).split("#")[0]
            # 判断路径是否存在
            scraper_path = Path(path)
            if not scraper_path.exists():
                logger.warning(f"媒体库刮削路径不存在：{path}")
                continue
            logger.info(f"开始检索目录：{path} {mtype} ...")
            # 遍历所有文件
            files = SystemUtils.list_files(scraper_path, settings.RMT_MEDIAEXT)
            for file_path in files:
                if self._event.is_set():
                    logger.info(f"媒体库刮削服务停止")
                    return
                # 排除目录
                exclude_flag = False
                for exclude_path in exclude_paths:
                    try:
                        if file_path.is_relative_to(Path(exclude_path)):
                            exclude_flag = True
                            break
                    except Exception as err:
                        print(str(err))
                if exclude_flag:
                    logger.debug(f"{file_path} 在排除目录中，跳过 ...")
                    continue
                # 识别是电影还是电视剧
                if not mtype:
                    file_meta = MetaInfoPath(file_path)
                    mtype = file_meta.type
                # 重命名格式
                rename_format = settings.TV_RENAME_FORMAT \
                    if mtype == MediaType.TV else settings.MOVIE_RENAME_FORMAT
                # 计算重命名中的文件夹层数
                rename_format_level = len(rename_format.split("/")) - 1
                if rename_format_level < 1:
                    continue
                # 取相对路径的第1层目录
                media_path = file_path.parents[rename_format_level - 1]
                dir_item = (media_path, mtype)
                if dir_item not in scraper_paths:
                    logger.info(f"发现目录：{dir_item}")
                    scraper_paths.append(dir_item)
        # 开始刮削
        if scraper_paths:
            for item in scraper_paths:
                logger.info(f"开始刮削目录：{item[0]} ...")
                self.__scrape_dir(path=item[0], mtype=item[1])
        else:
            logger.info(f"未发现需要刮削的目录")

    def __scrape_dir(self, path: Path, mtype: MediaType):
        """
        削刮一个目录，该目录必须是媒体文件目录
        """
        # 优先读取本地nfo文件
        tmdbid = None
        if mtype == MediaType.MOVIE:
            # 电影
            movie_nfo = path / "movie.nfo"
            if movie_nfo.exists():
                tmdbid = self.__get_tmdbid_from_nfo(movie_nfo)
            file_nfo = path / (path.stem + ".nfo")
            if not tmdbid and file_nfo.exists():
                tmdbid = self.__get_tmdbid_from_nfo(file_nfo)
        else:
            # 电视剧
            tv_nfo = path / "tvshow.nfo"
            if tv_nfo.exists():
                tmdbid = self.__get_tmdbid_from_nfo(tv_nfo)
        if tmdbid:
            # 按TMDBID识别
            logger.info(f"读取到本地nfo文件的tmdbid：{tmdbid}")
            mediainfo = self.chain.recognize_media(tmdbid=tmdbid, mtype=mtype)
        else:
            # 按名称识别
            meta = MetaInfoPath(path)
            meta.type = mtype
            mediainfo = self.chain.recognize_media(meta=meta)
        if not mediainfo:
            logger.warn(f"未识别到媒体信息：{path}")
            return

        # 如果未开启新增已入库媒体是否跟随TMDB信息变化则根据tmdbid查询之前的title
        if not settings.SCRAP_FOLLOW_TMDB:
            transfer_history = TransferHistoryOper().get_by_type_tmdbid(tmdbid=mediainfo.tmdb_id,
                                                                        mtype=mediainfo.type.value)
            if transfer_history:
                mediainfo.title = transfer_history.title
        # 获取图片
        self.chain.obtain_images(mediainfo)

        self.scrape_metadata(
            fileitem=schemas.FileItem(
                storage="local",
                type="dir",
                path=str(path).replace("\\", "/") + "/",
                name=path.name,
                basename=path.stem,
                modify_time=path.stat().st_mtime,
            ),
            mediainfo=mediainfo,
            overwrite=True if self._mode else False
        )
        logger.info(f"{path} 刮削完成")

    def scrape_metadata(self, fileitem: schemas.FileItem,
                        meta: MetaBase = None, mediainfo: MediaInfo = None,
                        init_folder: bool = True, parent: schemas.FileItem = None,
                        overwrite: bool = False):
        """
        手动刮削媒体信息
        :param fileitem: 刮削目录或文件
        :param meta: 元数据
        :param mediainfo: 媒体信息
        :param init_folder: 是否刮削根目录
        :param parent: 上级目录
        :param overwrite: 是否覆盖已有文件
        """

        def is_bluray_folder(_fileitem: schemas.FileItem) -> bool:
            """
            判断是否为原盘目录
            """
            if not _fileitem or _fileitem.type != "dir":
                return False
            # 蓝光原盘目录必备的文件或文件夹
            required_files = ['BDMV', 'CERTIFICATE']
            # 检查目录下是否存在所需文件或文件夹
            for item in self.storagechain.list_files(_fileitem):
                if item.name in required_files:
                    return True
            return False

        def __list_files(_fileitem: schemas.FileItem):
            """
            列出下级文件
            """
            return self.storagechain.list_files(fileitem=_fileitem)

        def __save_file(_fileitem: schemas.FileItem, _path: Path, _content: Union[bytes, str]):
            """
            保存或上传文件
            :param _fileitem: 关联的媒体文件项
            :param _path: 元数据文件路径
            :param _content: 文件内容
            """
            if not _fileitem or not _content or not _path:
                return
            # 保存文件到临时目录，文件名随机
            tmp_file = settings.TEMP_PATH / f"{_path.name}.{StringUtils.generate_random_str(10)}"
            tmp_file.write_bytes(_content)
            # 获取文件的父目录
            try:
                item = self.storagechain.upload_file(fileitem=_fileitem, path=tmp_file, new_name=_path.name)
                if item:
                    logger.info(f"已保存文件：{item.path}")
                else:
                    logger.warn(f"文件保存失败：{_path}")
            finally:
                if tmp_file.exists():
                    tmp_file.unlink()

        def __download_image(_url: str) -> Optional[bytes]:
            """
            下载图片并保存
            """
            try:
                logger.info(f"正在下载图片：{_url} ...")
                r = RequestUtils(proxies=settings.PROXY).get_res(url=_url)
                if r:
                    return r.content
                else:
                    logger.info(f"{_url} 图片下载失败，请检查网络连通性！")
            except Exception as err:
                logger.error(f"{_url} 图片下载失败：{str(err)}！")
            return None

        # 当前文件路径
        filepath = Path(fileitem.path)
        if fileitem.type == "file" \
                and (not filepath.suffix or filepath.suffix.lower() not in settings.RMT_MEDIAEXT):
            return
        if not meta:
            meta = MetaInfoPath(filepath)
        if not mediainfo:
            mediainfo = MediaChain().recognize_by_meta(meta)
        if not mediainfo:
            logger.warn(f"{filepath} 无法识别文件媒体信息！")
            return
        logger.info(f"开始刮削：{filepath} ...")
        if mediainfo.type == MediaType.MOVIE:
            # 电影
            if fileitem.type == "file":
                # 是否已存在
                nfo_path = filepath.with_suffix(".nfo")
                if self.__check_time_out(nfo_path, self._pre_day):
                    logger.info(f"超过{self._pre_day}天跳过：{nfo_path}")
                    return

                if overwrite or not self.storagechain.get_file_item(storage=fileitem.storage, path=nfo_path):
                    # 电影文件
                    movie_nfo = MediaChain().metadata_nfo(meta=meta, mediainfo=mediainfo)
                    if movie_nfo:
                        # 保存或上传nfo文件到上级目录
                        __save_file(_fileitem=parent, _path=nfo_path, _content=movie_nfo)
                    else:
                        logger.warn(f"{filepath.name} nfo文件生成失败！")
                else:
                    logger.info(f"已存在nfo文件：{nfo_path}")
            else:
                # 电影目录
                if is_bluray_folder(fileitem):
                    # 原盘目录
                    nfo_path = filepath / (filepath.name + ".nfo")
                    if self.__check_time_out(nfo_path, self._pre_day):
                        logger.info(f"超过{self._pre_day}天跳过：{nfo_path}")
                        return
                    if overwrite or not self.storagechain.get_file_item(storage=fileitem.storage, path=nfo_path):
                        # 生成原盘nfo
                        movie_nfo = MediaChain().metadata_nfo(meta=meta, mediainfo=mediainfo)
                        if movie_nfo:
                            # 保存或上传nfo文件到当前目录
                            __save_file(_fileitem=fileitem, _path=nfo_path, _content=movie_nfo)
                        else:
                            logger.warn(f"{filepath.name} nfo文件生成失败！")
                    else:
                        logger.info(f"已存在nfo文件：{nfo_path}")
                else:
                    # 处理目录内的文件
                    files = __list_files(_fileitem=fileitem)
                    for file in files:
                        self.scrape_metadata(fileitem=file,
                                             meta=meta, mediainfo=mediainfo,
                                             init_folder=False, parent=fileitem,
                                             overwrite=overwrite)
                # 生成目录内图片文件
                if init_folder:
                    # 图片
                    for attr_name, attr_value in vars(mediainfo).items():
                        if attr_value \
                                and attr_name.endswith("_path") \
                                and attr_value \
                                and isinstance(attr_value, str) \
                                and attr_value.startswith("http"):
                            image_name = attr_name.replace("_path", "") + Path(attr_value).suffix
                            image_path = filepath / image_name
                            if not self.storagechain.get_file_item(storage=fileitem.storage,
                                                                                path=image_path):
                                # 下载图片
                                content = __download_image(_url=attr_value)
                                # 写入图片到当前目录
                                if content:
                                    __save_file(_fileitem=fileitem, _path=image_path, _content=content)
                            else:
                                logger.info(f"已存在图片文件：{image_path}")
        else:
            # 电视剧
            if fileitem.type == "file":
                # 重新识别季集
                file_meta = MetaInfoPath(filepath)
                if not file_meta.begin_episode:
                    logger.warn(f"{filepath.name} 无法识别文件集数！")
                    return
                file_mediainfo = MediaChain().recognize_media(meta=file_meta, tmdbid=mediainfo.tmdb_id,
                                                      episode_group=mediainfo.episode_group)
                if not file_mediainfo:
                    logger.warn(f"{filepath.name} 无法识别文件媒体信息！")
                    return
                # 是否已存在
                nfo_path = filepath.with_suffix(".nfo")

                if self.__check_time_out(nfo_path, self._pre_day):
                    logger.info(f"超过{self._pre_day}天跳过：{nfo_path}")
                    return

                if overwrite or not self.storagechain.get_file_item(storage=fileitem.storage, path=nfo_path):
                    # 获取集的nfo文件
                    episode_nfo = MediaChain().metadata_nfo(meta=file_meta, mediainfo=file_mediainfo,
                                                    season=file_meta.begin_season,
                                                    episode=file_meta.begin_episode)
                    if episode_nfo:
                        # 保存或上传nfo文件到上级目录
                        if not parent:
                            parent = self.storagechain.get_parent_item(fileitem)
                        __save_file(_fileitem=parent, _path=nfo_path, _content=episode_nfo)
                    else:
                        logger.warn(f"{filepath.name} nfo文件生成失败！")
                else:
                    logger.info(f"已存在nfo文件：{nfo_path}")
                # 获取集的图片
                image_dict = MediaChain().metadata_img(mediainfo=file_mediainfo,
                                               season=file_meta.begin_season, episode=file_meta.begin_episode)
                if image_dict:
                    for episode, image_url in image_dict.items():
                        image_path = filepath.with_suffix(Path(image_url).suffix)
                        if not self.storagechain.get_file_item(storage=fileitem.storage, path=image_path):
                            # 下载图片
                            content = __download_image(image_url)
                            # 保存图片文件到当前目录
                            if content:
                                if not parent:
                                    parent = self.storagechain.get_parent_item(fileitem)
                                __save_file(_fileitem=parent, _path=image_path, _content=content)
                        else:
                            logger.info(f"已存在图片文件：{image_path}")
            else:
                # 当前为目录，处理目录内的文件
                files = __list_files(_fileitem=fileitem)
                for file in files:
                    self.scrape_metadata(fileitem=file,
                                         meta=meta, mediainfo=mediainfo,
                                         parent=fileitem if file.type == "file" else None,
                                         init_folder=True if file.type == "dir" else False,
                                         overwrite=overwrite)
                # 生成目录的nfo和图片
                if init_folder:
                    # 识别文件夹名称
                    season_meta = MetaInfo(filepath.name)
                    # 当前文件夹为Specials或者SPs时，设置为S0
                    if filepath.name in settings.RENAME_FORMAT_S0_NAMES:
                        season_meta.begin_season = 0
                    if season_meta.begin_season is not None:
                        # 是否已存在
                        nfo_path = filepath / "season.nfo"
                        if self.__check_time_out(nfo_path, self._pre_day):
                            logger.info(f"超过{self._pre_day}天跳过：{nfo_path}")
                            return
                        if overwrite or not self.storagechain.get_file_item(storage=fileitem.storage, path=nfo_path):
                            # 当前目录有季号，生成季nfo
                            season_nfo = MediaChain().metadata_nfo(meta=meta, mediainfo=mediainfo,
                                                           season=season_meta.begin_season)
                            if season_nfo:
                                # 写入nfo到根目录
                                __save_file(_fileitem=fileitem, _path=nfo_path, _content=season_nfo)
                            else:
                                logger.warn(f"无法生成电视剧季nfo文件：{meta.name}")
                        else:
                            logger.info(f"已存在nfo文件：{nfo_path}")
                        # TMDB季poster图片
                        image_dict = MediaChain().metadata_img(mediainfo=mediainfo, season=season_meta.begin_season)
                        if image_dict:
                            for image_name, image_url in image_dict.items():
                                image_path = filepath.with_name(image_name)
                                if not self.storagechain.get_file_item(storage=fileitem.storage,
                                                                                    path=image_path):
                                    # 下载图片
                                    content = __download_image(image_url)
                                    # 保存图片文件到剧集目录
                                    if content:
                                        if not parent:
                                            parent = self.storagechain.get_parent_item(fileitem)
                                        __save_file(_fileitem=parent, _path=image_path, _content=content)
                                else:
                                    logger.info(f"已存在图片文件：{image_path}")
                        # 额外fanart季图片：poster thumb banner
                        image_dict = MediaChain().metadata_img(mediainfo=mediainfo)
                        if image_dict:
                            for image_name, image_url in image_dict.items():
                                if image_name.startswith("season"):
                                    image_path = filepath.with_name(image_name)
                                    # 只下载当前刮削季的图片
                                    image_season = "00" if "specials" in image_name else image_name[6:8]
                                    if image_season != str(season_meta.begin_season).rjust(2, '0'):
                                        logger.info(f"当前刮削季为：{season_meta.begin_season}，跳过文件：{image_path}")
                                        continue
                                    if not self.storagechain.get_file_item(storage=fileitem.storage,
                                                                                        path=image_path):
                                        # 下载图片
                                        content = __download_image(image_url)
                                        # 保存图片文件到当前目录
                                        if content:
                                            if not parent:
                                                parent = self.storagechain.get_parent_item(fileitem)
                                            __save_file(_fileitem=parent, _path=image_path, _content=content)
                                    else:
                                        logger.info(f"已存在图片文件：{image_path}")
                    # 判断当前目录是不是剧集根目录
                    if not season_meta.season:
                        # 是否已存在
                        nfo_path = filepath / "tvshow.nfo"
                        if self.__check_time_out(nfo_path, self._pre_day):
                            logger.info(f"超过{self._pre_day}天跳过：{nfo_path}")
                            return
                        if overwrite or not self.storagechain.get_file_item(storage=fileitem.storage, path=nfo_path):
                            # 当前目录有名称，生成tvshow nfo 和 tv图片
                            tv_nfo = MediaChain().metadata_nfo(meta=meta, mediainfo=mediainfo)
                            if tv_nfo:
                                # 写入tvshow nfo到根目录
                                __save_file(_fileitem=fileitem, _path=nfo_path, _content=tv_nfo)
                            else:
                                logger.warn(f"无法生成电视剧nfo文件：{meta.name}")
                        else:
                            logger.info(f"已存在nfo文件：{nfo_path}")
                        # 生成目录图片
                        image_dict = MediaChain().metadata_img(mediainfo=mediainfo)
                        if image_dict:
                            for image_name, image_url in image_dict.items():
                                # 不下载季图片
                                if image_name.startswith("season"):
                                    continue
                                image_path = filepath / image_name
                                if not self.storagechain.get_file_item(storage=fileitem.storage,
                                                                                    path=image_path):
                                    # 下载图片
                                    content = __download_image(image_url)
                                    # 保存图片文件到当前目录
                                    if content:
                                        __save_file(_fileitem=fileitem, _path=image_path, _content=content)
                                else:
                                    logger.info(f"已存在图片文件：{image_path}")
        logger.info(f"{filepath.name} 刮削完成")


    @staticmethod
    def __get_tmdbid_from_nfo(file_path: Path):
        """
        从nfo文件中获取信息
        :param file_path:
        :return: tmdbid
        """
        if not file_path:
            return None
        xpaths = [
            "uniqueid[@type='Tmdb']",
            "uniqueid[@type='tmdb']",
            "uniqueid[@type='TMDB']",
            "tmdbid"
        ]
        try:
            reader = NfoReader(file_path)
            for xpath in xpaths:
                tmdbid = reader.get_element_value(xpath)
                if tmdbid:
                    return tmdbid
        except Exception as err:
            logger.warn(f"从nfo文件中获取tmdbid失败：{str(err)}")
        return None

    @staticmethod
    def __check_time_out(file_path: Path, pre_day: int):
        """
        从nfo文件中获取信息
        :param file_path:
        :return: dateadded
        """
        if not file_path:
            return None
        xpaths = [
            "dateadded"
        ]
        try:
            reader = NfoReader(file_path)
            for xpath in xpaths:
                dateadded = reader.get_element_value(xpath)
                if dateadded:
                    target_time = datetime.strptime(dateadded, "%Y-%m-%d %H:%M:%S")
                    now = datetime.now()
                    seven_days_ago = now - timedelta(days=pre_day)
                    # 超过7天前，返回True，否则返回False
                    return target_time < seven_days_ago
        except Exception as err:
            logger.warn(f"从nfo文件中获取dateadded失败：{str(err)}")
        return None

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))
