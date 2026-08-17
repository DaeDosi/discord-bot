"""싱드컵 갤럭시 시즌 — 치지직 **공식 예선 참가자 명단**(백엔드 사본).

정본은 `web/frontend/lib/singcupQualifiers.ts`이고 이 파일은 거기서 생성한 사본이다.
백엔드에도 필요한 이유는 하나다: **"공식 예선 참가자에게만 LIVE를 노출한다"를
서버에서 강제하기 위해서**다. 프런트에서만 거르면 응답에는 비참가자가 계속
들어 있어, 화면 한 줄만 바뀌면 정책이 조용히 풀린다.

**손으로 고치지 말 것.** 정본을 고친 뒤 다시 생성한다. 두 파일이 어긋나면
`tests/test_singcup_qualifiers_sync.py`가 개수·id 집합으로 잡아낸다.

부문 키(`female_solo`/`male_solo`/`groups`)는 PIKU 매핑의 부문 키와 **같은 값**이다
(`singcup_piku.DIVISIONS`). 두 곳이 갈라지면 매핑이 조용히 어긋난다.
"""

QUALIFIERS: dict = {
    "female_solo": [
        {
            "order": 1,
            "name": "고다요",
            "channelId": "54cf8e05daaaa9577ad0f211d495dc95"
        },
        {
            "order": 2,
            "name": "김아테 l Ate",
            "channelId": "f42e97f59c3177b8686dccfbf90792dd"
        },
        {
            "order": 3,
            "name": "냐오 NYAO",
            "channelId": "79ef1f5274b48fcb2de41b8ac8ea7ca1"
        },
        {
            "order": 4,
            "name": "리모 RIMO",
            "channelId": "4a0493dd6f3542b99943c39848ad1045"
        },
        {
            "order": 5,
            "name": "린시",
            "channelId": "54bdb327ed6039db869d15b0e5eec394"
        },
        {
            "order": 6,
            "name": "마무 Mamuu",
            "channelId": "902744ac298720925b661711d76f7133"
        },
        {
            "order": 7,
            "name": "마우쥐",
            "channelId": "b2854dc0735e55fa86c53bd15242d30f"
        },
        {
            "order": 8,
            "name": "매화린 Maehwarin",
            "channelId": "c62ac389ebfc8942302298df5e9cd71b"
        },
        {
            "order": 9,
            "name": "모이 M0I",
            "channelId": "be059789e19ac3e46198e15911a80122"
        },
        {
            "order": 10,
            "name": "모코 파르페",
            "channelId": "d20f164fe5946abe0bce2e5a43052923"
        },
        {
            "order": 11,
            "name": "미네르 Mineru",
            "channelId": "aa6e91dc838cdf50996ffdb86f5854be"
        },
        {
            "order": 12,
            "name": "미하루 Miharu",
            "channelId": "088973112d8acc831ec20274f7ffbb99"
        },
        {
            "order": 13,
            "name": "반 희",
            "channelId": "0c061f7f79909af482d9e66879d2225c"
        },
        {
            "order": 14,
            "name": "백희 Baekhee",
            "channelId": "d916dc846dcbe66a3482e238625b9022"
        },
        {
            "order": 15,
            "name": "비화 Bihwa",
            "channelId": "521b37e8cd74b933501d3d1ad4d5031c"
        },
        {
            "order": 16,
            "name": "산 SAN",
            "channelId": "40132b11317951dafff8c1a770b34b63"
        },
        {
            "order": 17,
            "name": "삿땅 Sattan",
            "channelId": "cdedd10b0a6c0c007a028a4f15c82ad1"
        },
        {
            "order": 18,
            "name": "샤메이",
            "channelId": "6d16804cf98da47ba82bd13c0c029723"
        },
        {
            "order": 19,
            "name": "세이로쿠 키라",
            "channelId": "f63f66f701a9af347c668f3056cdaa9a"
        },
        {
            "order": 20,
            "name": "소봄",
            "channelId": "b3e9f222e434ae3016276987bf080eb4"
        },
        {
            "order": 21,
            "name": "소우선",
            "channelId": "8840e6587106713e56f3267a193ae6b8"
        },
        {
            "order": 22,
            "name": "쇼코코 도리",
            "channelId": "cb0ceb32cbf6bced3bac7892cbca37e1"
        },
        {
            "order": 23,
            "name": "아루네 arune",
            "channelId": "7edea32c0c19842f07c6a1e323a5b15a"
        },
        {
            "order": 24,
            "name": "아마네 나기 Planeta",
            "channelId": "941ea3807ba8b9b7dddb1670e3e7e5af"
        },
        {
            "order": 25,
            "name": "아오토라 유키",
            "channelId": "1e09ba347161a434e40b8245e0bf3cb3"
        },
        {
            "order": 26,
            "name": "아호밍 AHOMING",
            "channelId": "df0553bcb31247eaad8292166d0e421b"
        },
        {
            "order": 27,
            "name": "앵차루",
            "channelId": "5aa661567c8c14713a164759d21c036d"
        },
        {
            "order": 28,
            "name": "양메이",
            "channelId": "f1869f490ddd660c420b2f57c649e6bb"
        },
        {
            "order": 29,
            "name": "연하늘 HaneuI",
            "channelId": "b8b36da0d46acde123539f53daa23f7d"
        },
        {
            "order": 30,
            "name": "오단밍",
            "channelId": "f3b204dd3fd6925835ca1848cd4b6d3c"
        },
        {
            "order": 31,
            "name": "옥우",
            "channelId": "73d006ad5ed12000547433ed33a68247"
        },
        {
            "order": 32,
            "name": "온 하루",
            "channelId": "0f61ae00c2aef2b789dc009e51cbcc5a"
        },
        {
            "order": 33,
            "name": "온치",
            "channelId": "afe7bad3f54d9542cd3d0a30e38aa8ed"
        },
        {
            "order": 34,
            "name": "유달린",
            "channelId": "844d222a850ac5822a9a68870a24b786"
        },
        {
            "order": 35,
            "name": "유레카 님",
            "channelId": "3d5546fc8d0dcb478c973a9bc1328980"
        },
        {
            "order": 36,
            "name": "유메노 시로코",
            "channelId": "34d7c5fc0acaddc2e38053e5e54d771b"
        },
        {
            "order": 37,
            "name": "유엘 Yuel",
            "channelId": "2626f1dee4cedd89ecc8c4c313e37ecc"
        },
        {
            "order": 38,
            "name": "유피 UPI",
            "channelId": "d264bede9f1690ea1ff776309f8e2589"
        },
        {
            "order": 39,
            "name": "유할매",
            "channelId": "2c0c0ff859f6cb8045a3cdf99b3b9b54"
        },
        {
            "order": 40,
            "name": "윤키키",
            "channelId": "fedb21a1e1d5f9862eb47b15c69beadd"
        },
        {
            "order": 41,
            "name": "이 늘",
            "channelId": "1482d68b3478d2962e9d000ed6a33167"
        },
        {
            "order": 42,
            "name": "이 소 에",
            "channelId": "343c202c69ba6d11b7ec51741f9591ac"
        },
        {
            "order": 43,
            "name": "진짜루비",
            "channelId": "3d08f17e7ec1e2fb258f76c35d992160"
        },
        {
            "order": 44,
            "name": "청목 Chungmok",
            "channelId": "42344dd53d4c5dc33e962c8913f6adc3"
        },
        {
            "order": 45,
            "name": "쵸꾸미",
            "channelId": "f61107c04a4ae129873da5a5a25e2527"
        },
        {
            "order": 46,
            "name": "치치 Planeta",
            "channelId": "d5e2e0c14dcca4c4b10c7c9633022f52"
        },
        {
            "order": 47,
            "name": "칠찡나",
            "channelId": "cb2d7db1fe51bfc3e7b6ff438fd66ee3"
        },
        {
            "order": 48,
            "name": "카가야키 노바",
            "channelId": "b73696a325726b5aba0c391b4c70ab46"
        },
        {
            "order": 49,
            "name": "카네코 파냐 Planeta",
            "channelId": "5ead7124638ac4c568f2cde0224b3b6b"
        },
        {
            "order": 50,
            "name": "코오리 세라 Kori Serah",
            "channelId": "0d4e078913f04f0412f0092e10974492"
        },
        {
            "order": 51,
            "name": "쿠레나이 나츠키",
            "channelId": "a54372e8197f6d241a43a318279860d6"
        },
        {
            "order": 52,
            "name": "쿠미 란케",
            "channelId": "c21373316247091b1dfe344e878d0e41"
        },
        {
            "order": 53,
            "name": "쿠뽀미",
            "channelId": "73fc52c403eb3485524c02e1a0f2e623"
        },
        {
            "order": 54,
            "name": "쿠온 레이 Planeta",
            "channelId": "59aa824e4c4a56dd51e7a5e2e9172648"
        },
        {
            "order": 55,
            "name": "키미도리 메로",
            "channelId": "33d58e8eb2014d6e6c5eb74dcc587ebd"
        },
        {
            "order": 56,
            "name": "키쿄 KIKYO",
            "channelId": "df0f1ea25a23e05cf2ef031c2d807067"
        },
        {
            "order": 57,
            "name": "테리 눈나",
            "channelId": "0a2020b09b8cc7f2285b7ae5de2ce4d3"
        },
        {
            "order": 58,
            "name": "텐플라스텐 텐텐",
            "channelId": "88eb7dda524ca626ad3e08359b182900"
        },
        {
            "order": 59,
            "name": "플레디 PLEDDY",
            "channelId": "51211b5f8f3fe094f3740780aea32db0"
        },
        {
            "order": 60,
            "name": "하렌 루베오스",
            "channelId": "11f9a14c3439b7b1ceadd819d61624da"
        },
        {
            "order": 61,
            "name": "하카세 유우",
            "channelId": "3ab5d2f7a10442389c660ab0e4451622"
        },
        {
            "order": 62,
            "name": "한량1234",
            "channelId": "d409fe40356ee1ebfacbe2dd460ad573"
        },
        {
            "order": 63,
            "name": "호시에 제로",
            "channelId": "5786141997728cc2fcd660a15ef5f3af"
        },
        {
            "order": 64,
            "name": "RED레드",
            "channelId": "a96cea2d2c39cec636ba8170c66a0510"
        }
    ],
    "male_solo": [
        {
            "order": 1,
            "name": "강해온",
            "channelId": "e24d8a6e6f04c4f113f50f38028405c7"
        },
        {
            "order": 2,
            "name": "규비 KYUBI",
            "channelId": "28cbc9a7f252b4bccf6fc479caca686e"
        },
        {
            "order": 3,
            "name": "김 동 백",
            "channelId": "8e5e48f6aff1f698c53309c4ba582553"
        },
        {
            "order": 4,
            "name": "김 열 정",
            "channelId": "7c3c0cb8f3a5498b7a2956ef42e0b414"
        },
        {
            "order": 5,
            "name": "김 재 우",
            "channelId": "4d3c00056220010aea0411b454ab57f1"
        },
        {
            "order": 6,
            "name": "김카인 Cains",
            "channelId": "f696add177b036a5cdd288748d6200e5"
        },
        {
            "order": 7,
            "name": "노 아즈",
            "channelId": "2fdf1c0fe5f498dc6857f66fa0442c8b"
        },
        {
            "order": 8,
            "name": "노래맛 김쿠키",
            "channelId": "f8f9c0d0029b58c79eb6070ff501cac1"
        },
        {
            "order": 9,
            "name": "도진 YLIF",
            "channelId": "2e9602da2a4897bcfd8e08015e2c65a5"
        },
        {
            "order": 10,
            "name": "두간",
            "channelId": "07bba51bf0a233f3f44b54431704b190"
        },
        {
            "order": 11,
            "name": "들따때",
            "channelId": "3f4c4eb3ad63464a4f4a2f28517dfd8d"
        },
        {
            "order": 12,
            "name": "디안 dian",
            "channelId": "cf6cd035dad7d3854ffcdc07ed4c77ad"
        },
        {
            "order": 13,
            "name": "디온 Dion",
            "channelId": "f380f620615fffd54451032686932f9d"
        },
        {
            "order": 14,
            "name": "뚜우비",
            "channelId": "98bf268c594aa7a14f234296eca399e4"
        },
        {
            "order": 15,
            "name": "란돈",
            "channelId": "e3d5c3218579a4902213b67d0acff24c"
        },
        {
            "order": 16,
            "name": "랩 소디",
            "channelId": "d904f6c5605818e3a34560f061d8dbcb"
        },
        {
            "order": 17,
            "name": "마이시네",
            "channelId": "1c3b5788e820a5cd622aaba8daaaf343"
        },
        {
            "order": 18,
            "name": "매드해터",
            "channelId": "94f662d396e1eca8f4d41b956a01844c"
        },
        {
            "order": 19,
            "name": "모노 맥거핀",
            "channelId": "aa4e4434b4b9ca335011a8c61b9531d3"
        },
        {
            "order": 20,
            "name": "문시우 Si0w0",
            "channelId": "14f87cd9b5114589f49deb1ab501b574"
        },
        {
            "order": 21,
            "name": "미우 ME W",
            "channelId": "1e2555cbdf56ff1add40e5c9a5b59851"
        },
        {
            "order": 22,
            "name": "밀로 Millo",
            "channelId": "e999d11761cdce088d9af3fcd2f42a86"
        },
        {
            "order": 23,
            "name": "밀밭o",
            "channelId": "78e4761c91cf528767272b4c143d0393"
        },
        {
            "order": 24,
            "name": "박검복",
            "channelId": "77a81ceffdab49f687a3e39ddf04a6fe"
        },
        {
            "order": 25,
            "name": "백호범이",
            "channelId": "41a1ae4d44647340bd2be35d5ee434d2"
        },
        {
            "order": 26,
            "name": "보 라 곰",
            "channelId": "58ff653b1ca8ff2c7f352dab48a8f9e4"
        },
        {
            "order": 27,
            "name": "서노SEONO",
            "channelId": "14b342d1a0fb8a388cf8fba189bb5153"
        },
        {
            "order": 28,
            "name": "소이루",
            "channelId": "aaa0a5597d055ab3e33b22207dbb6f24"
        },
        {
            "order": 29,
            "name": "송쿤",
            "channelId": "bbd2d3a7abc36bc1d71fab75c35fa0e7"
        },
        {
            "order": 30,
            "name": "수녕",
            "channelId": "df4eb2d62da0a45ef8b37b5948126fcb"
        },
        {
            "order": 31,
            "name": "시엘 캣츠아이",
            "channelId": "e785798f1e776d12110be5e68daf9873"
        },
        {
            "order": 32,
            "name": "아이넬과쇼코",
            "channelId": "11adbdccc0f12e458ce95e0b2be42323"
        },
        {
            "order": 33,
            "name": "알로애 Aloe",
            "channelId": "39fa62e81c7d74d9d18610fa6e3d5bdb"
        },
        {
            "order": 34,
            "name": "애불",
            "channelId": "592514aff364a66ecbc94a924b5f52c4"
        },
        {
            "order": 35,
            "name": "여뭉 toastybear",
            "channelId": "49af53f86b47b0bdf93ffca77d4d801c"
        },
        {
            "order": 36,
            "name": "여주 YLIF",
            "channelId": "f8c781ada0626bd8d2d3ed4e403cc375"
        },
        {
            "order": 37,
            "name": "옥수햄",
            "channelId": "57e8328f10ac641682a32f0d57c143fe"
        },
        {
            "order": 38,
            "name": "우마왕 Woomawang",
            "channelId": "1985d2f95b3438b941f3bc411cba7236"
        },
        {
            "order": 39,
            "name": "우유총각",
            "channelId": "6dd1e21513fdf43b36b91b38e205dbed"
        },
        {
            "order": 40,
            "name": "유 성운",
            "channelId": "c09abfa1abc8cc94f97810d035ed5e53"
        },
        {
            "order": 41,
            "name": "유넬 Yunell",
            "channelId": "fa1b3b9f8927bdc99ac44dabf1f794a9"
        },
        {
            "order": 42,
            "name": "유람 Yuram",
            "channelId": "5d68142291810fa1a2a0dfa90a688362"
        },
        {
            "order": 43,
            "name": "윤민들레",
            "channelId": "9acde8ff4437d4b751fbfab7a020675e"
        },
        {
            "order": 44,
            "name": "이오 군",
            "channelId": "ac80f85cb849fb584c02e2cb2af6be7a"
        },
        {
            "order": 45,
            "name": "제미르 ZEMIR",
            "channelId": "96323616ff7dda48c2b1f9a9f940bc39"
        },
        {
            "order": 46,
            "name": "제인 Zane",
            "channelId": "f500490b0b9d77ac1ee2dace7f4d208a"
        },
        {
            "order": 47,
            "name": "제일리",
            "channelId": "9d865b511f02025292d27b6cb9074c2a"
        },
        {
            "order": 48,
            "name": "준갬",
            "channelId": "8d7c970c50889f351e1ce3a8e6dbe93d"
        },
        {
            "order": 49,
            "name": "초깨비",
            "channelId": "58fbc220638009db28385db1112d22df"
        },
        {
            "order": 50,
            "name": "카든이",
            "channelId": "2a9e91108f2a96e7bd8e685f9b4339a7"
        },
        {
            "order": 51,
            "name": "콩쥐 KongJi",
            "channelId": "b1f0c7bf431fa5bbeeab4101f6fe5169"
        },
        {
            "order": 52,
            "name": "킹끄끄",
            "channelId": "edbd742b5e23b6b698f531638eb3c7ec"
        },
        {
            "order": 53,
            "name": "타키 Taki",
            "channelId": "23388f4bd11195a570b0884464ba5d5c"
        },
        {
            "order": 54,
            "name": "펩 코 이",
            "channelId": "786552f2d5b9c6f3507f3d8e0a34288a"
        },
        {
            "order": 55,
            "name": "피 네",
            "channelId": "d8a26244f9cfda1a9250583ad7293427"
        },
        {
            "order": 56,
            "name": "하안 HAAN",
            "channelId": "bd435a37373e596d583ed6fd2f17d2f3"
        },
        {
            "order": 57,
            "name": "해든 YLIF",
            "channelId": "522359c96788ba8e9823799b58e099a0"
        },
        {
            "order": 58,
            "name": "휘운",
            "channelId": "5d7f8a961cecff7b7e071f71eaa93593"
        },
        {
            "order": 59,
            "name": "흑 주",
            "channelId": "8746f51a23aad61a7cac34ddb1c1d05b"
        },
        {
            "order": 60,
            "name": "IM 한결",
            "channelId": "be7db546b3de12f3e1658a68f4949251"
        },
        {
            "order": 61,
            "name": "Im 마린",
            "channelId": "81685b7160d757e1b29305dc0a30b9e8"
        },
        {
            "order": 62,
            "name": "JiNiE 지니 I 에델리안",
            "channelId": "939efd46507c177fb35f803777602c5b"
        },
        {
            "order": 63,
            "name": "K나그네",
            "channelId": "a1a148a62040fa35e609eb9c4e9c5804"
        },
        {
            "order": 64,
            "name": "Syze사이즈",
            "channelId": "2c33aeb0435762a95aa829b23e76ef6a"
        }
    ],
    "groups": [
        {
            "teamNumber": 1,
            "groupEntryId": "g1",
            "members": [
                {
                    "order": 1,
                    "name": "공 운",
                    "channelId": "b6475394b5c812be2e712095f8604db2"
                },
                {
                    "order": 2,
                    "name": "김 이 든",
                    "channelId": "7b624aeb24c6a6be26eb2dea5b6ac69f"
                },
                {
                    "order": 3,
                    "name": "시 키 Siki",
                    "channelId": "e5e69fafebfeaf98a9717dec24a0f6fe"
                }
            ]
        },
        {
            "teamNumber": 2,
            "groupEntryId": "g2",
            "members": [
                {
                    "order": 1,
                    "name": "구이룬",
                    "channelId": "1965959830702dce3b657129dd8a6b8c"
                },
                {
                    "order": 2,
                    "name": "아시모프A158",
                    "channelId": "efc60c0f95ce3be7f91e9da4a5f2d99c"
                }
            ]
        },
        {
            "teamNumber": 3,
            "groupEntryId": "g3",
            "members": [
                {
                    "order": 1,
                    "name": "김니디",
                    "channelId": "9116ce342b24d085f3afe73ccc465465"
                },
                {
                    "order": 2,
                    "name": "슈향",
                    "channelId": "57482ec9b07718076a1692bc210d5fa0"
                },
                {
                    "order": 3,
                    "name": "이 선",
                    "channelId": "cffac6a96b6a2f625db9e6085c40d1c1"
                },
                {
                    "order": 4,
                    "name": "조별하",
                    "channelId": "ead28b71f3fdd5e8b52321825217a065"
                }
            ]
        },
        {
            "teamNumber": 4,
            "groupEntryId": "g4",
            "members": [
                {
                    "order": 1,
                    "name": "꾸이링",
                    "channelId": "27b38f9ce948643d639f6abfc2788b15"
                },
                {
                    "order": 2,
                    "name": "시라이 쿄카",
                    "channelId": "540369f6aea657761b250a6ee383a406"
                }
            ]
        },
        {
            "teamNumber": 5,
            "groupEntryId": "g5",
            "members": [
                {
                    "order": 1,
                    "name": "나기 히유라",
                    "channelId": "cf6389ea2b91e3a3ef2df8d9ea71c1c5"
                },
                {
                    "order": 2,
                    "name": "아루테미 슈 SHU",
                    "channelId": "aec70225ee6f7ce910a26084f95d6f60"
                }
            ]
        },
        {
            "teamNumber": 6,
            "groupEntryId": "g6",
            "members": [
                {
                    "order": 1,
                    "name": "난 에궁",
                    "channelId": "9f673229b644e81373811f7e272b2098"
                },
                {
                    "order": 2,
                    "name": "무개냥",
                    "channelId": "8258938ce8070864e5018e43fdbcef2c"
                }
            ]
        },
        {
            "teamNumber": 7,
            "groupEntryId": "g7",
            "members": [
                {
                    "order": 1,
                    "name": "난바다",
                    "channelId": "b1ac916d727d32cb7f472b50f6f97654"
                },
                {
                    "order": 2,
                    "name": "조하구1",
                    "channelId": "c9e23f9b2d858601ac5f20f46634cce2"
                },
                {
                    "order": 3,
                    "name": "해득이",
                    "channelId": "659dcc02fa42de17387db4c8d722f55c"
                }
            ]
        },
        {
            "teamNumber": 8,
            "groupEntryId": "g8",
            "members": [
                {
                    "order": 1,
                    "name": "냐페르",
                    "channelId": "5d6a0908bb4eac47f3d839cbffe60961"
                },
                {
                    "order": 2,
                    "name": "디아푸",
                    "channelId": "50bc69419921ed333fee0a0c854076e5"
                },
                {
                    "order": 3,
                    "name": "선아린",
                    "channelId": "5891784ca4e2320cfa25668f1f247579"
                }
            ]
        },
        {
            "teamNumber": 9,
            "groupEntryId": "g9",
            "members": [
                {
                    "order": 1,
                    "name": "돈도로다단",
                    "channelId": "684027b72e41f8895400b1f813c465b6"
                },
                {
                    "order": 2,
                    "name": "버음바",
                    "channelId": "71fe514d31377367a83a8bfaf720c783"
                }
            ]
        },
        {
            "teamNumber": 10,
            "groupEntryId": "g10",
            "members": [
                {
                    "order": 1,
                    "name": "동동씨",
                    "channelId": "0baca7f3a4b838eb4e65d2068a189b19"
                },
                {
                    "order": 2,
                    "name": "호잇뚠",
                    "channelId": "df5b281407e63b91ca80e90b0cd1afc6"
                }
            ]
        },
        {
            "teamNumber": 11,
            "groupEntryId": "g11",
            "members": [
                {
                    "order": 1,
                    "name": "디플라",
                    "channelId": "3860a8c5c9ea7cf9b7e0f89a5abced9a"
                },
                {
                    "order": 2,
                    "name": "레노떼",
                    "channelId": "775961416cd479ee1de7b5aabc73b5ce"
                }
            ]
        },
        {
            "teamNumber": 12,
            "groupEntryId": "g12",
            "members": [
                {
                    "order": 1,
                    "name": "레드오랭지",
                    "channelId": "e0ecdf2c06a74ea5b1fc6b7e8d626dd6"
                },
                {
                    "order": 2,
                    "name": "레츠 Letsu",
                    "channelId": "f9666b790723630d4c5e73525f968f94"
                }
            ]
        },
        {
            "teamNumber": 13,
            "groupEntryId": "g13",
            "members": [
                {
                    "order": 1,
                    "name": "마호노 레비 Orbi",
                    "channelId": "dd34d48439b7feee08b53cb4063cb0fd"
                },
                {
                    "order": 2,
                    "name": "모모메이 유메 Orbi",
                    "channelId": "3bc90b22f6b7925e5b92767562f44f3b"
                },
                {
                    "order": 3,
                    "name": "코오모리 히키 Orbi",
                    "channelId": "cfb55fb02bdcbf17c3eb0bd756ee7762"
                }
            ]
        },
        {
            "teamNumber": 14,
            "groupEntryId": "g14",
            "members": [
                {
                    "order": 1,
                    "name": "맥문동",
                    "channelId": "733c98be047f710d3b1bc7a27b0c83e2"
                },
                {
                    "order": 2,
                    "name": "PROJECT8",
                    "channelId": "41db3bd7760c7b813b0f06df1876e1af"
                }
            ]
        },
        {
            "teamNumber": 15,
            "groupEntryId": "g15",
            "members": [
                {
                    "order": 1,
                    "name": "므므네 mumune",
                    "channelId": "23a256033aae1c6324cec9a69458791d"
                },
                {
                    "order": 2,
                    "name": "아일라 Iyla",
                    "channelId": "ff182197eed405b1130d22aa3a4fefa0"
                }
            ]
        },
        {
            "teamNumber": 16,
            "groupEntryId": "g16",
            "members": [
                {
                    "order": 1,
                    "name": "미로 사쿠",
                    "channelId": "3c143d7505da6cf4a092d62690460b3f"
                },
                {
                    "order": 2,
                    "name": "트리시kr",
                    "channelId": "fd63dc8ed9457fcf190dbd71978e32fa"
                },
                {
                    "order": 3,
                    "name": "폴리모프 P0lym0rph",
                    "channelId": "918da8b2f34e8adeb1a32e10e4c1d4e3"
                }
            ]
        },
        {
            "teamNumber": 17,
            "groupEntryId": "g17",
            "members": [
                {
                    "order": 1,
                    "name": "미사키 하루",
                    "channelId": "9f7467f5f3dfa4ea3dcef2962187d6a2"
                },
                {
                    "order": 2,
                    "name": "슨아",
                    "channelId": "3fefbab86746791039760a3ce516f1da"
                }
            ]
        },
        {
            "teamNumber": 18,
            "groupEntryId": "g18",
            "members": [
                {
                    "order": 1,
                    "name": "베이야",
                    "channelId": "c4a63fcc6fc5f6c2da7692e6dcbba96a"
                },
                {
                    "order": 2,
                    "name": "에스타",
                    "channelId": "b41937fced2370c1912e6949b4c59227"
                }
            ]
        },
        {
            "teamNumber": 19,
            "groupEntryId": "g19",
            "members": [
                {
                    "order": 1,
                    "name": "사이네코 코나타 Wishing",
                    "channelId": "dc5a830020254936843b8afeed98949f"
                },
                {
                    "order": 2,
                    "name": "쿠릉 Kurung",
                    "channelId": "ab52ab49739163b108e253c4f038dcec"
                }
            ]
        },
        {
            "teamNumber": 20,
            "groupEntryId": "g20",
            "members": [
                {
                    "order": 1,
                    "name": "시라하 마노",
                    "channelId": "ea852f54a083225ae86ec5df85bdceb5"
                },
                {
                    "order": 2,
                    "name": "아샤 누아르",
                    "channelId": "562958e2539ea1e44b93ae2adb51d1a1"
                },
                {
                    "order": 3,
                    "name": "이 치 치",
                    "channelId": "cfccab4af1801bfd217076c559650278"
                }
            ]
        },
        {
            "teamNumber": 21,
            "groupEntryId": "g21",
            "members": [
                {
                    "order": 1,
                    "name": "양담비",
                    "channelId": "21e45f2d67f33b783e58a0823ac43d2b"
                },
                {
                    "order": 2,
                    "name": "코우세이 우키",
                    "channelId": "1b8fadbb141d246c88a8a722ef3bb06f"
                }
            ]
        },
        {
            "teamNumber": 22,
            "groupEntryId": "g22",
            "members": [
                {
                    "order": 1,
                    "name": "온유 OnU",
                    "channelId": "2f15b1272b730b79a43a0bddf3b5d1dd"
                },
                {
                    "order": 2,
                    "name": "이소 2SO",
                    "channelId": "e62b2771a14fb902fba8b3b324d59e45"
                }
            ]
        },
        {
            "teamNumber": 23,
            "groupEntryId": "g23",
            "members": [
                {
                    "order": 1,
                    "name": "온하얀 ONHAYAN",
                    "channelId": "91caa53fc6cf5ee3cdbc802bd23bf155"
                },
                {
                    "order": 2,
                    "name": "유레이 UREI",
                    "channelId": "4f650f02bc4ab38a998d74e3abb1b68b"
                },
                {
                    "order": 3,
                    "name": "이루네 IRUNE",
                    "channelId": "e984779fd445e71bfd8c99106e432bf1"
                },
                {
                    "order": 4,
                    "name": "하나빈 HANAVIN",
                    "channelId": "7ca6c5f45a9b16f75970f54c309623c0"
                }
            ]
        },
        {
            "teamNumber": 24,
            "groupEntryId": "g24",
            "members": [
                {
                    "order": 1,
                    "name": "요라밍Yoraming",
                    "channelId": "06530ce4e3782aa09d80a4989b3b5243"
                },
                {
                    "order": 2,
                    "name": "판구리",
                    "channelId": "ed3f2b9e1f3df94090cc0e21bce64519"
                }
            ]
        },
        {
            "teamNumber": 25,
            "groupEntryId": "g25",
            "members": [
                {
                    "order": 1,
                    "name": "은초이 Choy",
                    "channelId": "fd8516eb8d31a8a5147e94c281ae3f07"
                },
                {
                    "order": 2,
                    "name": "조이람",
                    "channelId": "e66acc8276b509151dbdc3f9a4477dbc"
                }
            ]
        },
        {
            "teamNumber": 26,
            "groupEntryId": "g26",
            "members": [
                {
                    "order": 1,
                    "name": "이 로 지",
                    "channelId": "e3e50ec6c85c2a286c32acd6da83b697"
                },
                {
                    "order": 2,
                    "name": "하루시카 시나",
                    "channelId": "571e7d61e19e43331bc87035dca4b56f"
                }
            ]
        },
        {
            "teamNumber": 27,
            "groupEntryId": "g27",
            "members": [
                {
                    "order": 1,
                    "name": "이리을 LEERIEUL",
                    "channelId": "15025d616518761242b9e05b41cd4cd4"
                },
                {
                    "order": 2,
                    "name": "후츄후츄",
                    "channelId": "8b14aeaab59c1896ff9350a4a03515d3"
                }
            ]
        },
        {
            "teamNumber": 28,
            "groupEntryId": "g28",
            "members": [
                {
                    "order": 1,
                    "name": "초슈야",
                    "channelId": "2589896226ea56ac81886a7b46103975"
                },
                {
                    "order": 2,
                    "name": "흑지로",
                    "channelId": "61acacd56ff16f2b5cd09aa8b9d5002d"
                }
            ]
        },
        {
            "teamNumber": 29,
            "groupEntryId": "g29",
            "members": [
                {
                    "order": 1,
                    "name": "치카치카 쵸케",
                    "channelId": "573994c85d061e56952d7b2e0483d012"
                },
                {
                    "order": 2,
                    "name": "코네코토 스야",
                    "channelId": "076b5984789b249599352ab8bd8420be"
                }
            ]
        },
        {
            "teamNumber": 30,
            "groupEntryId": "g30",
            "members": [
                {
                    "order": 1,
                    "name": "폰루루 ㅣ PON RuRu",
                    "channelId": "051a3090c9566bdfd6af01cb1f463c16"
                }
            ]
        },
        {
            "teamNumber": 31,
            "groupEntryId": "g31",
            "members": [
                {
                    "order": 1,
                    "name": "한 유 월",
                    "channelId": "0feaf00a7322954b2da161d401e09dc8"
                },
                {
                    "order": 2,
                    "name": "RuriHana",
                    "channelId": "24eef70586192ec0dcc5df0df2a56b16"
                }
            ]
        },
        {
            "teamNumber": 32,
            "groupEntryId": "g32",
            "members": [
                {
                    "order": 1,
                    "name": "호시유메 루나",
                    "channelId": "162b203fac941457a4b3d4c708051585"
                },
                {
                    "order": 2,
                    "name": "후와리 미토미",
                    "channelId": "9db6c061100d0afeaf6ec94dfb3cdb42"
                }
            ]
        }
    ]
}

#: 부문 → 채널 id 집합. LIVE 노출 판정에 쓴다.
def channel_ids(division: str | None = None) -> set[str]:
    keys = [division] if division else list(QUALIFIERS)
    out: set[str] = set()
    for k in keys:
        for row in QUALIFIERS.get(k, []):
            if k == "groups":
                out.update(m["channelId"] for m in row["members"])
            else:
                out.add(row["channelId"])
    return out


#: 전체 참가자 채널 id(부문 무관). 모듈 로드 시 한 번만 만든다.
ALL_CHANNEL_IDS: frozenset = frozenset(channel_ids())


def division_of(channel_id: str) -> str | None:
    """이 채널이 속한 부문. 여러 부문에 있으면 solo를 우선한다(그룹 겸업 대비)."""
    for k in ("female_solo", "male_solo"):
        if any(r["channelId"] == channel_id for r in QUALIFIERS[k]):
            return k
    for g in QUALIFIERS["groups"]:
        if any(m["channelId"] == channel_id for m in g["members"]):
            return "groups"
    return None
