/*
This script configures Nexus so that Cachito can stage JavaScript content for the Cachito request.

This script creates an NPM proxy repository that proxies requests to the cachito-js NPM repository group.

No permissions are configured since it is expected that Cachito's Nexus service account has access to use all NPM
repositories managed by the Nexus instance.
 */
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.repository.config.Configuration


// Scope logger to the script using @Field
@Field final Logger logger = LoggerFactory.getLogger('cachito');


def createRequestRepo(String repositoryName, String httpUsername, String httpPassword) {
    /*
        Here are some of the properties that can be set:
          repositoryName: name,
          recipeName: recipeName,
          online: true,
          attributes: [
            httpclient: [
              authentication: <See Below>,
              autoBlock: <Boolean>,
              blocked: <Boolean>,
              connection: https://bit.ly/39hLQBZ
            ],
            proxy: [
              remoteUrl: <String>,
              contentMaxAge: <Integer>,
              metadataMaxAge: <Integer>
            ],
            negativeCache: [
              enabled: <Boolean>,
              timeToLive: <Integer>
            ],
            storage: [
              blobStoreName: <String>,
              strictContentTypeValidation: <Boolean>
            ]
          ]

        httpclient.authentication (select from one of the following types):
        - username: https://bit.ly/2WMAxPB
        - ntlm: https://bit.ly/3bC87w7
        - bearerToken: https://bit.ly/2QL4egm

        For example:
          def authentication = repo_config.attributes('httpclient').child('authentication')
          authentication.set('type', 'username')
          authentication.set('username', 'cachito')
          authentication.set('password', 'secret_password')
     */
    // This object is provided by Nexus when the script is executed
    def repositoryManager = repository.repositoryManager

    Configuration repoConfig
    Boolean exists = repositoryManager.exists(repositoryName)
    if(exists) {
        logger.info("Modifying the repository ${repositoryName}")
        repoConfig = repositoryManager.get(repositoryName).configuration
    }
    else {
        logger.info("Creating the repository ${repositoryName}")
        repoConfig = repositoryManager.newConfiguration()
        repoConfig.repositoryName = repositoryName
        repoConfig.recipeName = 'npm-proxy'
        repoConfig.attributes('storage').set('blobStoreName', 'cachito-js')
    }

    repoConfig.online = true
    repoConfig.attributes('storage').set('strictContentTypeValidation', true)
    // This URL is from the perspective of the Nexus instance
    repoConfig.attributes('proxy').set('remoteUrl', 'http://localhost:8081/repository/cachito-js/')

    def httpclient = repoConfig.attributes('httpclient')
    // Don't block connections to cachito-js if it is down
    httpclient.set('autoBlock', false)
    // Don't block connections to cachito-js
    httpclient.set('blocked', false)

    // This is the authentication required for this proxy to access the cachito-js NPM repository group
    def authentication = httpclient.child('authentication')
    authentication.set('type', 'username');
    authentication.set('username', httpUsername)
    authentication.set('password', httpPassword)

    if(exists) {
        repositoryManager.update(repoConfig)
    }
    else {
        repositoryManager.create(repoConfig)
    }
}


// http_username and http_password are the credentials to connect to the cachito-js NPM repository group
request = new JsonSlurper().parseText(args)
['repository_name', 'http_password', 'http_username'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

createRequestRepo(request.repository_name, request.http_username, request.http_password)

return 'The repository was created successfully'
